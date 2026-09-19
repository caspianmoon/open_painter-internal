"""Export publication transaction: the hardening plan's fault matrix (H1) against the real
``open_painter.export.transaction`` module, with hard process termination at every boundary.

Every case starts from known bytes (originals A/B, replacements, competing files) and checks
the resulting file hashes, not only exceptions. Hard interruptions run the actual commit in a
subprocess of this interpreter with OP_EXPORT_FAILPOINT set; recovery then runs in this
process. Filesystem scope: the temporary directory of this machine (local APFS on the
reference machine).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from open_painter.export import transaction  # noqa: E402

PAYLOADS = {"old_a": b"old A\n" * 200, "old_b": b"old B\n" * 300, "new_a": b"new A\n" * 200, "new_b": b"new B\n" * 300,
            "new_c": b"new C\n" * 100, "foreign": b"not ours\n" * 50}
HASHES = {name: hashlib.sha256(data).hexdigest() for name, data in PAYLOADS.items()}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(folder):
    return {p.name: sha(p) for p in sorted(Path(folder).iterdir()) if p.is_file()}


def leftovers(folder):
    return sorted(p.name for p in Path(folder).iterdir()
                  if transaction.BACKUP_SUFFIX in p.name or transaction.STAGING_SUFFIX in p.name
                  or p.name in {transaction.JOURNAL, transaction.LOCK} or p.name.endswith(".tmp"))


SUBPROCESS = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from open_painter.export import transaction
spec = json.loads(Path(sys.argv[2]).read_text())
tx = transaction.Transaction(identifier=spec["identifier"])
for destination, source in spec["items"]:
    tx.add(destination, source)
outcome = tx.commit(overwrite=spec["overwrite"])
print(json.dumps({"destinations": [str(p) for p in outcome.destinations], "cleanup_pending": outcome.cleanup_pending,
                  "message": outcome.message}))
'''


class Scene:
    """An output folder with originals A and B, and staged replacements A, B and a new C."""

    def __init__(self, folders=1):
        self.tmp = tempfile.TemporaryDirectory(prefix="op-tx-")
        self.root = Path(self.tmp.name)
        self.out = self.root / "out"
        self.out.mkdir()
        self.staging = self.root / "staging"
        self.staging.mkdir()
        (self.out / "A.png").write_bytes(PAYLOADS["old_a"])
        (self.out / "B.png").write_bytes(PAYLOADS["old_b"])
        self.items = []
        for name, key in (("A.png", "new_a"), ("B.png", "new_b"), ("C.png", "new_c")):
            source = self.staging / name
            source.write_bytes(PAYLOADS[key])
            self.items.append((self.out / name, source))
        self.other = None
        if folders > 1:
            self.other = self.root / "other"
            self.other.mkdir()
            (self.other / "D.png").write_bytes(PAYLOADS["old_a"])
            source = self.staging / "D.png"
            source.write_bytes(PAYLOADS["new_b"])
            self.items.append((self.other / "D.png", source))

    def transaction(self, identifier=None):
        tx = transaction.Transaction(**({"identifier": identifier} if identifier else {}))
        for destination, source in self.items:
            tx.add(destination, source)
        return tx

    def run_subprocess(self, failpoint, overwrite=True, identifier="feedfacecafe"):
        spec = self.root / "spec.json"
        spec.write_text(json.dumps({"identifier": identifier, "overwrite": overwrite,
                                    "items": [(str(d), str(s)) for d, s in self.items]}))
        env = dict(os.environ, OP_EXPORT_FAILPOINT=failpoint)
        env.pop("OP_EXPORT_FAULT", None)
        return subprocess.run([sys.executable, "-c", SUBPROCESS, str(Path(__file__).resolve().parents[2] / "src"), str(spec)],
                              env=env, capture_output=True, text=True)

    def close(self):
        self.tmp.cleanup()


ORIGINAL = {"A.png": HASHES["old_a"], "B.png": HASHES["old_b"]}
COMMITTED = {"A.png": HASHES["new_a"], "B.png": HASHES["new_b"], "C.png": HASHES["new_c"]}


class TransactionTests(unittest.TestCase):
    def setUp(self):
        transaction._held_locks.clear()

    # ---- plain success -------------------------------------------------------------------------------
    def test_commit_replaces_and_cleans_up(self):
        scene = Scene()
        try:
            outcome = scene.transaction().commit(overwrite=True)
            self.assertEqual(outcome.cleanup_pending, [])
            self.assertEqual(snapshot(scene.out), COMMITTED)
            self.assertEqual(leftovers(scene.out), [])
        finally:
            scene.close()

    def test_no_overwrite_refuses_existing(self):
        scene = Scene()
        try:
            with self.assertRaises(ValueError):
                scene.transaction().commit(overwrite=False)
            self.assertEqual(snapshot(scene.out), ORIGINAL)
            self.assertEqual(leftovers(scene.out), [])
        finally:
            scene.close()

    # ---- cleanup failure after the durable decision --------------------------------------------------
    def test_cleanup_failure_keeps_every_committed_output(self):
        scene = Scene()
        try:
            os.environ[transaction.FAULT_VARIABLE] = "cleanup"     # B's backup removal fails after A's went
            try:
                outcome = scene.transaction().commit(overwrite=True)
            finally:
                os.environ.pop(transaction.FAULT_VARIABLE, None)
            self.assertTrue(outcome.cleanup_pending, outcome)
            self.assertIn("cleanup pending", outcome.message)
            files = snapshot(scene.out)
            self.assertEqual({k: files[k] for k in COMMITTED}, COMMITTED)
            self.assertIn(transaction.JOURNAL, files)
            # Recovery (idempotent) finishes the housekeeping and keeps all new files.
            for _ in range(2):
                actions = transaction.recover(scene.out)
            self.assertEqual(snapshot(scene.out), COMMITTED)
            self.assertEqual(leftovers(scene.out), [])
            self.assertEqual(transaction.recover(scene.out), [])
        finally:
            scene.close()

    # ---- hard interruptions at every boundary -----------------------------------------------------------
    def _interrupt(self, failpoint, expected, overwrite=True, folders=1):
        scene = Scene(folders=folders)
        try:
            completed = scene.run_subprocess(failpoint, overwrite=overwrite)
            self.assertEqual(completed.returncode, 3, (failpoint, completed.stdout, completed.stderr))
            before = snapshot(scene.out)
            actions = transaction.recover(scene.out)
            self.assertEqual({k: v for k, v in snapshot(scene.out).items() if k in expected}, expected, (failpoint, before, actions))
            self.assertEqual(leftovers(scene.out), [], (failpoint, actions))
            if scene.other is not None:
                self.assertEqual(leftovers(scene.other), [], failpoint)
            # Running recovery again changes nothing.
            self.assertEqual(transaction.recover(scene.out), [])
            self.assertEqual({k: v for k, v in snapshot(scene.out).items() if k in expected}, expected)
            return scene, actions
        except BaseException:
            scene.close()
            raise
        else:
            scene.close()

    def test_interrupt_after_prepared_restores_originals(self):
        scene, actions = self._interrupt("after_prepared", ORIGINAL)
        self.assertEqual(snapshot(scene.out), ORIGINAL)
        scene.close()

    def test_interrupt_after_first_backup_restores_originals(self):
        scene, _ = self._interrupt("after_first_backup", ORIGINAL)
        self.assertEqual(snapshot(scene.out), ORIGINAL)
        scene.close()

    def test_interrupt_after_backups_restores_originals(self):
        scene, _ = self._interrupt("after_backups", ORIGINAL)
        self.assertEqual(snapshot(scene.out), ORIGINAL)
        scene.close()

    def test_interrupt_after_first_publish_restores_originals(self):
        scene, _ = self._interrupt("after_first_publish", ORIGINAL)
        self.assertEqual(snapshot(scene.out), ORIGINAL, "the published A must go, B and the absent C must not appear")
        scene.close()

    def test_interrupt_before_commit_decision_restores_originals(self):
        scene, _ = self._interrupt("before_commit_decision", ORIGINAL)
        self.assertEqual(snapshot(scene.out), ORIGINAL)
        scene.close()

    def test_interrupt_after_commit_decision_keeps_new_state(self):
        scene, _ = self._interrupt("after_commit_decision", COMMITTED)
        self.assertEqual(snapshot(scene.out), COMMITTED)
        scene.close()

    def test_interrupt_after_first_backup_removed_keeps_new_state(self):
        scene, _ = self._interrupt("after_first_backup_removed", COMMITTED)
        self.assertEqual(snapshot(scene.out), COMMITTED)
        scene.close()

    def test_two_folders_one_outcome_from_either_folder(self):
        for failpoint, expected_out, expected_other in (("after_backups", ORIGINAL, {"D.png": HASHES["old_a"]}),
                                                          ("after_commit_decision", COMMITTED, {"D.png": HASHES["new_b"]})):
            for start in ("out", "other"):
                scene = Scene(folders=2)
                try:
                    completed = scene.run_subprocess(failpoint)
                    self.assertEqual(completed.returncode, 3, completed.stderr)
                    transaction.recover(getattr(scene, start))
                    self.assertEqual(snapshot(scene.out), expected_out, (failpoint, start))
                    self.assertEqual(snapshot(scene.other), expected_other, (failpoint, start))
                    self.assertEqual(leftovers(scene.out), [])
                    self.assertEqual(leftovers(scene.other), [])
                finally:
                    scene.close()

    def test_recovery_interrupted_and_restarted(self):
        """A recovery cut short leaves the decision in place; the next run completes it."""
        scene = Scene()
        try:
            completed = scene.run_subprocess("after_commit_decision")
            self.assertEqual(completed.returncode, 3)
            real_unlink = Path.unlink
            calls = {"n": 0}

            def failing_unlink(self, missing_ok=False):
                if transaction.BACKUP_SUFFIX in self.name:
                    calls["n"] += 1
                    if calls["n"] == 1:
                        raise OSError("injected: interrupted during recovery")
                return real_unlink(self, missing_ok=missing_ok)
            Path.unlink = failing_unlink
            try:
                with self.assertRaises(transaction.RecoveryRequired):
                    transaction.recover(scene.out)
            finally:
                Path.unlink = real_unlink
            self.assertIn(transaction.JOURNAL, snapshot(scene.out), "the decision must survive an interrupted recovery")
            transaction.recover(scene.out)
            transaction.recover(scene.out)
            self.assertEqual(snapshot(scene.out), COMMITTED)
            self.assertEqual(leftovers(scene.out), [])
        finally:
            scene.close()

    # ---- competing writers --------------------------------------------------------------------------------
    def test_competing_file_at_publish_is_preserved(self):
        scene = Scene()
        try:
            for path in (scene.out / "A.png", scene.out / "B.png"):
                path.unlink()
            real_copy = transaction._copy_flushed

            def racing_copy(source, target):
                destination = Path(str(target).split(transaction.STAGING_SUFFIX)[0])
                if destination.name == "B.png" and not destination.exists():
                    destination.write_bytes(PAYLOADS["foreign"])       # after validation, before the link
                return real_copy(source, target)
            transaction._copy_flushed = racing_copy
            try:
                with self.assertRaises(transaction.PublicationConflict):
                    scene.transaction().commit(overwrite=False)
            finally:
                transaction._copy_flushed = real_copy
            files = snapshot(scene.out)
            self.assertEqual(files.get("B.png"), HASHES["foreign"], "the competing file must be byte-identical")
            self.assertNotIn("A.png", files, "A, published by this request, is rolled back")
            self.assertNotIn("C.png", files)
            self.assertEqual(leftovers(scene.out), [])
        finally:
            scene.close()

    def test_exclusive_create_fallback_without_hard_links(self):
        """A volume that refuses hard links publishes by exclusive create: the same bytes land, and a
        competing file that appears before the publish is still never replaced."""
        import errno
        real_link = transaction.os.link

        def no_links(source, destination):
            raise OSError(errno.EPERM, "Operation not permitted")
        scene = Scene()
        try:
            transaction.os.link = no_links
            try:
                outcome = scene.transaction().commit(overwrite=True)
            finally:
                transaction.os.link = real_link
            self.assertEqual(snapshot(scene.out), COMMITTED)
            self.assertEqual(leftovers(scene.out), [])
            self.assertFalse(outcome.cleanup_pending)
        finally:
            scene.close()
        scene = Scene()
        try:
            for path in (scene.out / "A.png", scene.out / "B.png"):
                path.unlink()
            real_copy = transaction._copy_flushed

            def racing_copy(source, target):
                destination = Path(str(target).split(transaction.STAGING_SUFFIX)[0])
                if destination.name == "B.png" and not destination.exists():
                    destination.write_bytes(PAYLOADS["foreign"])
                return real_copy(source, target)
            transaction.os.link = no_links
            transaction._copy_flushed = racing_copy
            try:
                with self.assertRaises(transaction.PublicationConflict):
                    scene.transaction().commit(overwrite=False)
            finally:
                transaction.os.link = real_link
                transaction._copy_flushed = real_copy
            files = snapshot(scene.out)
            self.assertEqual(files.get("B.png"), HASHES["foreign"])
            self.assertNotIn("A.png", files)
            self.assertEqual(leftovers(scene.out), [])
        finally:
            scene.close()

    def test_competing_file_during_recovery_of_absent_path_is_kept(self):
        scene = Scene()
        try:
            completed = scene.run_subprocess("after_first_publish")     # A published (new), B/C not yet
            self.assertEqual(completed.returncode, 3)
            (scene.out / "C.png").write_bytes(PAYLOADS["foreign"])      # someone else creates C meanwhile
            with self.assertRaises(transaction.RecoveryRequired) as raised:
                transaction.recover(scene.out)
            self.assertIn("C.png", str(raised.exception))
            files = snapshot(scene.out)
            self.assertEqual(files["C.png"], HASHES["foreign"])
            self.assertIn(transaction.JOURNAL, files, "evidence is kept while a conflict stands")
            self.assertEqual(files["A.png"], HASHES["old_a"], "A's original was restored")
            (scene.out / "C.png").unlink()                              # the conflict resolved by hand
            transaction.recover(scene.out)
            self.assertEqual(snapshot(scene.out), ORIGINAL)
        finally:
            scene.close()

    def test_foreign_change_after_interrupted_publish_is_a_conflict(self):
        scene = Scene()
        try:
            completed = scene.run_subprocess("after_first_publish")
            self.assertEqual(completed.returncode, 3)
            (scene.out / "A.png").write_bytes(PAYLOADS["foreign"])      # an application edits the published A
            with self.assertRaises(transaction.RecoveryRequired):
                transaction.recover(scene.out)
            files = snapshot(scene.out)
            self.assertEqual(files["A.png"], HASHES["foreign"])
            self.assertTrue(any(transaction.BACKUP_SUFFIX in name for name in files), "A's backup stays")
        finally:
            scene.close()

    # ---- journal validation ---------------------------------------------------------------------------------
    def test_invalid_journals_are_kept_and_reported(self):
        cases = {
            "truncated": b'{"format": 2, "transaction": "feedfacecafe", "items": [',
            "unsupported_format": json.dumps({"format": 1, "transaction": "feedfacecafe", "items": []}).encode(),
            "escaping_path": json.dumps({"format": 2, "transaction": "feedfacecafe", "state": "PREPARED", "pid": 1,
                                         "coordinator": "", "folders": ["/"],
                                         "items": [{"name": "../etc/passwd", "folder": "/", "absent": True, "staging": None,
                                                    "backup": None, "staged_sha256": ""}]}).encode(),
            "unknown_state": json.dumps({"format": 2, "transaction": "feedfacecafe", "state": "MAYBE", "pid": 1,
                                         "coordinator": "", "folders": [], "items": []}).encode(),
        }
        for label, payload in cases.items():
            scene = Scene()
            try:
                backup = scene.out / f"A.png{transaction.BACKUP_SUFFIX}feedfacecafe"
                backup.write_bytes(PAYLOADS["old_a"])
                (scene.out / transaction.JOURNAL).write_bytes(payload)
                with self.assertRaises(transaction.RecoveryRequired, msg=label):
                    transaction.recover(scene.out)
                self.assertTrue((scene.out / transaction.JOURNAL).exists(), label)
                self.assertTrue(backup.exists(), label)
                with self.assertRaises(transaction.RecoveryRequired, msg=label):
                    scene.transaction().commit(overwrite=True)      # the next export refuses too
            finally:
                scene.close()

    def test_missing_coordinator_keeps_evidence(self):
        scene = Scene(folders=2)
        try:
            completed = scene.run_subprocess("after_backups")
            self.assertEqual(completed.returncode, 3)
            journal = json.loads((scene.out / transaction.JOURNAL).read_text())
            coordinator = Path(journal["coordinator"])
            participant = scene.other if coordinator.parent.resolve() == scene.out.resolve() else scene.out
            coordinator.unlink()                                        # the decision record vanishes
            with self.assertRaises(transaction.RecoveryRequired):
                transaction.recover(participant)
            self.assertTrue((participant / transaction.JOURNAL).exists())
            self.assertTrue(any(transaction.BACKUP_SUFFIX in name for name in snapshot(participant)))
        finally:
            scene.close()

    # ---- concurrency ---------------------------------------------------------------------------------------------
    def test_second_process_is_refused_while_one_publishes(self):
        scene = Scene()
        try:
            holder = subprocess.Popen([sys.executable, "-c", (
                "import sys, time; sys.path.insert(0, sys.argv[1]);"
                "from open_painter.export import transaction; l = transaction._Lock(sys.argv[2]).acquire(); print('held', flush=True); time.sleep(30)"),
                str(Path(__file__).resolve().parents[2] / "src"), str(scene.out)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "held")
                with self.assertRaises(transaction.ExportBusy):
                    scene.transaction().commit(overwrite=True)
                self.assertEqual({k: v for k, v in snapshot(scene.out).items() if k != transaction.LOCK}, ORIGINAL)
                (scene.out / transaction.JOURNAL).write_text(json.dumps({
                    "format": 2, "transaction": "feedfacecafe", "state": "PREPARED", "pid": holder.pid,
                    "coordinator": str(scene.out / transaction.JOURNAL), "folders": [str(scene.out)], "items": [], "created": 0}))
                with self.assertRaises(transaction.ExportBusy):
                    transaction.recover(scene.out)
                (scene.out / transaction.JOURNAL).unlink()
            finally:
                holder.kill()
                holder.wait()
            # The dead holder's lock is stale: the next export proceeds.
            outcome = scene.transaction().commit(overwrite=True)
            self.assertEqual(snapshot(scene.out), COMMITTED)
            self.assertEqual(leftovers(scene.out), [])
        finally:
            scene.close()

    def test_recovery_refuses_a_running_transaction(self):
        scene = Scene()
        try:
            # A journal of a live process with its lock present: not ours to recover.
            spec = {"format": 2, "transaction": "feedfacecafe", "state": "PREPARED", "pid": os.getppid() if os.getppid() > 1 else os.getpid(),
                    "coordinator": str(scene.out / transaction.JOURNAL), "folders": [str(scene.out)], "items": [], "created": 0}
            (scene.out / transaction.JOURNAL).write_text(json.dumps(spec))
            (scene.out / transaction.LOCK).write_text(f"{spec['pid']}\n0\n")
            with self.assertRaises(transaction.ExportBusy):
                transaction.recover(scene.out)
            self.assertTrue((scene.out / transaction.JOURNAL).exists())
        finally:
            scene.close()

    # ---- failures before the decision ----------------------------------------------------------------------------
    def test_failure_during_backup_or_publish_restores_originals(self):
        for fault in ("backup", "publish"):
            scene = Scene()
            try:
                os.environ[transaction.FAULT_VARIABLE] = fault
                try:
                    with self.assertRaises(OSError):
                        scene.transaction().commit(overwrite=True)
                finally:
                    os.environ.pop(transaction.FAULT_VARIABLE, None)
                self.assertEqual(snapshot(scene.out), ORIGINAL, fault)
                self.assertEqual(leftovers(scene.out), [], fault)
            finally:
                scene.close()

    def test_failed_restoration_keeps_backups_and_journal(self):
        scene = Scene()
        try:
            os.environ[transaction.FAULT_VARIABLE] = "publish"
            real_replace = os.replace

            def failing_replace(src, dst):
                if transaction.BACKUP_SUFFIX in str(src):
                    raise OSError("injected: cannot restore")
                return real_replace(src, dst)
            os.replace = failing_replace
            try:
                with self.assertRaises(transaction.RecoveryRequired):
                    scene.transaction().commit(overwrite=True)
            finally:
                os.replace = real_replace
                os.environ.pop(transaction.FAULT_VARIABLE, None)
            files = snapshot(scene.out)
            self.assertIn(transaction.JOURNAL, files)
            self.assertTrue(any(transaction.BACKUP_SUFFIX in name for name in files))
            transaction.recover(scene.out)
            self.assertEqual(snapshot(scene.out), ORIGINAL)
        finally:
            scene.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
