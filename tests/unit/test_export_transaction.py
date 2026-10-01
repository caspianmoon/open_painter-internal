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
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
if spec.get("recover"):
    print(json.dumps({"actions": transaction.recover(spec["recover"])}))
    sys.exit(0)
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

    def run_subprocess(self, failpoint, overwrite=True, identifier="feedfacecafe", fault=None, recover=None):
        """The real commit (or, with ``recover``, the real recovery of that folder) in a worker
        process of this interpreter, terminated hard at ``failpoint``. The worker and its files
        belong to this test: the folders are under this scene's temporary directory."""
        spec = self.root / "spec.json"
        spec.write_text(json.dumps({"identifier": identifier, "overwrite": overwrite, "recover": str(recover) if recover else None,
                                    "items": [(str(d), str(s)) for d, s in self.items]}), encoding="utf-8")
        env = dict(os.environ, OP_EXPORT_FAILPOINT=failpoint or "")
        env.pop("OP_EXPORT_FAULT", None)
        if fault:
            env["OP_EXPORT_FAULT"] = fault
        assert str(self.out).startswith(self.tmp.name) and (self.other is None or str(self.other).startswith(self.tmp.name))
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
            journal = json.loads((scene.out / transaction.JOURNAL).read_text(encoding="utf-8"))
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
                    "coordinator": str(scene.out / transaction.JOURNAL), "folders": [str(scene.out)], "items": [], "created": 0}), encoding="utf-8")
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
            (scene.out / transaction.JOURNAL).write_text(json.dumps(spec), encoding="utf-8")
            (scene.out / transaction.LOCK).write_text(f"{spec['pid']}\n0\n", encoding="utf-8")
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


class IdenticalScene(Scene):
    """The stability plan's payload set (S1 §5.3): A unchanged (its replacement has identical
    bytes), B changed, C newly created; with ``all_same`` every original equals its replacement;
    with two folders, D in the other folder is unchanged too."""

    def __init__(self, folders=1, all_same=False):
        super().__init__(folders=folders)
        (self.staging / "A.png").write_bytes(PAYLOADS["old_a"])
        if all_same:
            (self.staging / "B.png").write_bytes(PAYLOADS["old_b"])
        if self.other is not None:
            (self.staging / "D.png").write_bytes(PAYLOADS["old_a"])
        self.all_same = all_same
        self.originals = {"A.png": HASHES["old_a"], "B.png": HASHES["old_b"]}
        self.committed = {"A.png": HASHES["old_a"], "B.png": HASHES["old_b"] if all_same else HASHES["new_b"], "C.png": HASHES["new_c"]}


PRE_COMMIT = ("after_prepared", "after_staging", "after_first_backup", "after_backups", "after_first_publish", "before_commit_decision")


class IdenticalContentTests(unittest.TestCase):
    """Recovery when an original and its replacement hold the same bytes (stability plan S1):
    equal digests prove equality, never authorship. Every case runs the production commit in a
    worker process and the public ``recover`` afterwards, and asserts file sets and hashes."""

    def setUp(self):
        transaction._held_locks.clear()

    def _assert_pre_request(self, scene, label):
        self.assertEqual(snapshot(scene.out), scene.originals, label)
        self.assertEqual(leftovers(scene.out), [], label)
        if scene.other is not None:
            self.assertEqual(snapshot(scene.other), {"D.png": HASHES["old_a"]}, label)
            self.assertEqual(leftovers(scene.other), [], label)

    def test_unchanged_originals_survive_every_pre_commit_interruption(self):
        """The audit's first failure (after PREPARED, before any backup) and every later boundary:
        A (identical), B (changed) and D (identical, other folder) come back byte-identical; C never
        appears; a second recovery is a no-op."""
        for all_same in (False, True):
            for folders in (1, 2):
                for failpoint in PRE_COMMIT:
                    label = (failpoint, "all_same" if all_same else "mixed", folders)
                    scene = IdenticalScene(folders=folders, all_same=all_same)
                    try:
                        completed = scene.run_subprocess(failpoint)
                        self.assertEqual(completed.returncode, 3, (label, completed.stdout, completed.stderr))
                        self.assertTrue((scene.out / transaction.JOURNAL).exists(), label)
                        actions = transaction.recover(scene.out)
                        if failpoint in ("after_prepared", "after_staging", "after_first_backup", "after_backups"):
                            self.assertNotIn("removed unpublished A.png", actions, (label, actions))
                        self._assert_pre_request(scene, (label, actions))
                        self.assertEqual(transaction.recover(scene.out), [], label)
                        self._assert_pre_request(scene, label)
                    finally:
                        scene.close()

    def test_restored_identical_original_survives_failed_journal_cleanup(self):
        """The audit's second failure: recovery restores the original from its backup, journal
        removal fails; the next recovery must keep the restored original although it also matches
        the staged digest."""
        for failpoint in ("after_backups", "after_first_publish"):
            scene = IdenticalScene()
            try:
                completed = scene.run_subprocess(failpoint)
                self.assertEqual(completed.returncode, 3, completed.stderr)
                os.environ[transaction.FAULT_VARIABLE] = "recover_journal_removal"
                try:
                    with self.assertRaises(transaction.RecoveryRequired) as raised:
                        transaction.recover(scene.out)
                finally:
                    os.environ.pop(transaction.FAULT_VARIABLE, None)
                self.assertIn("run it again", str(raised.exception))
                files = snapshot(scene.out)
                self.assertEqual(files.get("A.png"), HASHES["old_a"], (failpoint, files))
                self.assertEqual(files.get("B.png"), HASHES["old_b"], (failpoint, files))
                self.assertIn(transaction.JOURNAL, files, "the decision stays until cleanup completes")
                for _ in range(2):
                    actions = transaction.recover(scene.out)
                    self.assertNotIn("removed unpublished A.png", actions, actions)
                    self._assert_pre_request(scene, (failpoint, actions))
            finally:
                scene.close()

    def test_hard_interruption_inside_recovery_converges(self):
        """Recovery itself terminated hard (after its first change, before journal removal, after
        the participants' journals went): repeating it reaches the same pre-request state."""
        for failpoint in ("recover_after_first_change", "recover_before_journal_removal", "recover_after_participant_journals"):
            for start in ("out", "other"):
                scene = IdenticalScene(folders=2)
                try:
                    completed = scene.run_subprocess("after_first_publish")
                    self.assertEqual(completed.returncode, 3, completed.stderr)
                    interrupted = scene.run_subprocess(failpoint, recover=getattr(scene, start))
                    self.assertEqual(interrupted.returncode, 3, (failpoint, start, interrupted.stdout, interrupted.stderr))
                    self.assertTrue((scene.out / transaction.JOURNAL).exists() or (scene.other / transaction.JOURNAL).exists(),
                                    (failpoint, "the decision must survive an interrupted recovery"))
                    for folder in (scene.out, scene.other, scene.out):
                        transaction.recover(folder)
                    self._assert_pre_request(scene, (failpoint, start))
                finally:
                    scene.close()

    def test_committed_outputs_survive_participant_journal_failure(self):
        """The coordinator's COMMITTED record is the decision: a participant journal that cannot be
        written afterwards is housekeeping, never a rollback."""
        scene = IdenticalScene(folders=2)
        try:
            os.environ[transaction.FAULT_VARIABLE] = "participant_journal"
            try:
                outcome = scene.transaction().commit(overwrite=True)
            finally:
                os.environ.pop(transaction.FAULT_VARIABLE, None)
            self.assertTrue(outcome.cleanup_pending, outcome)
            files = snapshot(scene.out)
            self.assertEqual({k: files[k] for k in scene.committed}, scene.committed)
            self.assertEqual(snapshot(scene.other).get("D.png"), HASHES["old_a"])
            for folder in (scene.other, scene.out):
                transaction.recover(folder)
            self.assertEqual(snapshot(scene.out), scene.committed)
            self.assertEqual(snapshot(scene.other), {"D.png": HASHES["old_a"]})
            self.assertEqual(leftovers(scene.out), [])
            self.assertEqual(leftovers(scene.other), [])
        finally:
            scene.close()

    def test_committed_outputs_survive_interruption_during_cleanup(self):
        for failpoint in ("after_commit_decision", "after_first_backup_removed"):
            scene = IdenticalScene(folders=2)
            try:
                completed = scene.run_subprocess(failpoint)
                self.assertEqual(completed.returncode, 3, completed.stderr)
                actions = transaction.recover(scene.other)
                self.assertIn("committed outputs kept", actions)
                self.assertEqual(snapshot(scene.out), scene.committed, failpoint)
                self.assertEqual(snapshot(scene.other), {"D.png": HASHES["old_a"]}, failpoint)
                self.assertEqual(leftovers(scene.out), [], failpoint)
                self.assertEqual(leftovers(scene.other), [], failpoint)
            finally:
                scene.close()

    def test_competing_file_with_our_bytes_at_publish_is_preserved(self):
        """A writer that creates the destination with exactly our bytes before the link: their
        file (same inode) stays; nothing of the request is published."""
        scene = IdenticalScene()
        try:
            for path in (scene.out / "A.png", scene.out / "B.png"):
                path.unlink()
            real_copy = transaction._copy_flushed
            inode = {}

            def racing_copy(source, target):
                destination = Path(str(target).split(transaction.STAGING_SUFFIX)[0])
                if destination.name == "C.png" and not destination.exists():
                    destination.write_bytes(PAYLOADS["new_c"])          # same bytes as ours
                    inode["C.png"] = destination.stat().st_ino
                return real_copy(source, target)
            transaction._copy_flushed = racing_copy
            try:
                with self.assertRaises(transaction.PublicationConflict):
                    scene.transaction().commit(overwrite=False)
            finally:
                transaction._copy_flushed = real_copy
            files = snapshot(scene.out)
            self.assertEqual(files, {"C.png": HASHES["new_c"]}, "only the competitor's C remains")
            self.assertEqual((scene.out / "C.png").stat().st_ino, inode["C.png"], "the competitor's own file, not a copy")
            self.assertEqual(leftovers(scene.out), [])
        finally:
            scene.close()

    def test_competing_file_after_backup_is_never_replaced(self):
        """With overwriting allowed, a file that appears at a destination after its original was
        moved aside is a competing writer's: the request rolls back and names it; the original
        stays in its backup for the artist."""
        scene = IdenticalScene()
        try:
            real_replace = os.replace

            def racing_replace(source, target):
                result = real_replace(source, target)
                if transaction.BACKUP_SUFFIX in str(target) and Path(target).name.startswith("B.png"):
                    Path(str(target).split(transaction.BACKUP_SUFFIX)[0]).write_bytes(PAYLOADS["foreign"])
                return result
            os.replace = racing_replace
            try:
                with self.assertRaises(transaction.RecoveryRequired) as raised:
                    scene.transaction().commit(overwrite=True)
            finally:
                os.replace = real_replace
            self.assertIn("B.png", str(raised.exception))
            files = snapshot(scene.out)
            self.assertEqual(files["B.png"], HASHES["foreign"])
            self.assertEqual(files["A.png"], HASHES["old_a"])
            self.assertNotIn("C.png", files)
            self.assertIn(f"B.png{transaction.BACKUP_SUFFIX}", "".join(files), "B's original stays in its backup")
            self.assertIn(transaction.JOURNAL, files)
            (scene.out / "B.png").unlink()                              # the artist removes the intruder
            transaction.recover(scene.out)
            self._assert_pre_request(scene, "after the conflict is resolved")
        finally:
            scene.close()

    def test_exclusive_create_fallback_interrupted(self):
        """No hard links: the destination is created exclusively, claimed in the journal, then
        filled. Interrupted before the claim the empty file is ambiguous and kept with an error;
        after the claim (empty or partly filled) it is ours and removed; a claimed file another
        writer replaced is not."""
        big = b"big C\n" * (300 * 1024)                                  # > one copy chunk: a real partial fill
        for failpoint, expectation in (("after_exclusive_create", "ambiguous"), ("after_exclusive_claim", "ours"),
                                       ("during_exclusive_fill", "ours"), ("during_exclusive_fill", "replaced")):
            scene = IdenticalScene()
            try:
                (scene.staging / "C.png").write_bytes(big)
                completed = scene.run_subprocess(failpoint, fault="no_hard_links")
                self.assertEqual(completed.returncode, 3, (failpoint, completed.stderr))
                files = snapshot(scene.out)
                if expectation == "ambiguous":
                    self.assertEqual((scene.out / "A.png").stat().st_size, 0, "A was created, not yet claimed")
                    with self.assertRaises(transaction.RecoveryRequired) as raised:
                        transaction.recover(scene.out)
                    self.assertIn("A.png is empty", str(raised.exception))
                    self.assertIn(transaction.JOURNAL, snapshot(scene.out))
                    (scene.out / "A.png").unlink()                      # resolved by hand
                    transaction.recover(scene.out)
                    self._assert_pre_request(scene, failpoint)
                    continue
                journal = json.loads((scene.out / transaction.JOURNAL).read_text(encoding="utf-8"))
                claimed = {item["name"] for item in journal["items"] if item.get("claimed")}
                self.assertEqual(claimed, {"A.png"}, (failpoint, journal["items"]))
                if failpoint == "during_exclusive_fill":
                    self.assertGreater((scene.out / "A.png").stat().st_size, 0)
                if expectation == "replaced":
                    (scene.out / "A.png").write_bytes(PAYLOADS["foreign"])
                    with self.assertRaises(transaction.RecoveryRequired) as raised:
                        transaction.recover(scene.out)
                    self.assertIn("A.png", str(raised.exception))
                    self.assertEqual(snapshot(scene.out)["A.png"], HASHES["foreign"])
                    self.assertTrue(any(name.startswith(f"A.png{transaction.BACKUP_SUFFIX}") for name in snapshot(scene.out)))
                    continue
                actions = transaction.recover(scene.out)
                self.assertIn("removed unpublished A.png", actions)
                self._assert_pre_request(scene, (failpoint, actions))
            finally:
                scene.close()
        scene = IdenticalScene()
        try:
            completed = scene.run_subprocess("", fault="no_hard_links")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(snapshot(scene.out), scene.committed)
            self.assertEqual(leftovers(scene.out), [])
        finally:
            scene.close()

    def test_missing_original_is_reported_not_restored(self):
        """A backup that vanished before recovery: the missing original is named, nothing else is
        touched, and no "restored" result accompanies it."""
        scene = IdenticalScene()
        try:
            completed = scene.run_subprocess("after_backups")
            self.assertEqual(completed.returncode, 3, completed.stderr)
            (scene.out / f"A.png{transaction.BACKUP_SUFFIX}feedfacecafe").unlink()
            with self.assertRaises(transaction.RecoveryRequired) as raised:
                transaction.recover(scene.out)
            self.assertIn("A.png existed before this export and is now missing", str(raised.exception))
            files = snapshot(scene.out)
            self.assertNotIn("A.png", files)
            self.assertEqual(files.get("B.png"), HASHES["old_b"], "B is restored regardless")
            self.assertIn(transaction.JOURNAL, files)
        finally:
            scene.close()

    def test_previous_format_journal_is_recovered_without_deleting_unproven_files(self):
        """A format-2 journal (siblings were removed at publication, so nothing proves a
        published file): the unchanged original is kept, the unproven new file is kept and named."""
        scene = IdenticalScene()
        try:
            (scene.out / "C.png").write_bytes(PAYLOADS["new_c"])          # published by the old implementation
            (scene.out / f"B.png{transaction.BACKUP_SUFFIX}feedfacecafe").write_bytes(PAYLOADS["old_b"])
            (scene.out / "B.png").write_bytes(PAYLOADS["new_b"])
            journal = {"format": 2, "transaction": "feedfacecafe", "state": "PREPARED", "pid": 1, "created": 0,
                       "coordinator": str(scene.out / transaction.JOURNAL), "folders": [str(scene.out)], "items": []}
            for name, absent, original, backup, staged in (("A.png", False, HASHES["old_a"], None, HASHES["old_a"]),
                                                            ("B.png", False, HASHES["old_b"], "B.png", HASHES["new_b"]),
                                                            ("C.png", True, None, None, HASHES["new_c"])):
                journal["items"].append({"name": name, "folder": str(scene.out), "absent": absent,
                                         "original": None if original is None else {"size": 0, "mtime_ns": 0, "sha256": original},
                                         "backup": f"{backup}{transaction.BACKUP_SUFFIX}feedfacecafe" if backup else None,
                                         "staging": f"{name}{transaction.STAGING_SUFFIX}feedfacecafe", "staged_sha256": staged,
                                         "published_sha256": None, "method": None})
            (scene.out / transaction.JOURNAL).write_text(json.dumps(journal), encoding="utf-8")
            with self.assertRaises(transaction.RecoveryRequired) as raised:
                transaction.recover(scene.out)
            message = str(raised.exception)
            self.assertIn("C.png has this export's bytes but cannot be attributed", message)
            self.assertIn("B.png has this export's bytes but cannot be attributed", message)
            files = snapshot(scene.out)
            self.assertEqual(files["A.png"], HASHES["old_a"], "the unchanged original stays")
            self.assertEqual(files["C.png"], HASHES["new_c"])
            self.assertEqual(files["B.png"], HASHES["new_b"])
            self.assertEqual(files[f"B.png{transaction.BACKUP_SUFFIX}feedfacecafe"], HASHES["old_b"], "B's original stays in its backup")
            self.assertIn(transaction.JOURNAL, files)
            for name in ("B.png", "C.png"):
                (scene.out / name).unlink()                              # resolved by hand
            transaction.recover(scene.out)
            self._assert_pre_request(scene, "old journal after manual resolution")
        finally:
            scene.close()

    def test_successful_export_with_identical_and_changed_outputs(self):
        for all_same in (False, True):
            for folders in (1, 2):
                scene = IdenticalScene(folders=folders, all_same=all_same)
                try:
                    outcome = scene.transaction().commit(overwrite=True)
                    self.assertEqual(outcome.cleanup_pending, [])
                    self.assertEqual(outcome.message, "Export completed")
                    self.assertEqual(snapshot(scene.out), scene.committed, (all_same, folders))
                    self.assertEqual(leftovers(scene.out), [])
                    if scene.other is not None:
                        self.assertEqual(snapshot(scene.other), {"D.png": HASHES["old_a"]})
                        self.assertEqual(leftovers(scene.other), [])
                    self.assertEqual(transaction.recover(scene.out), [])
                finally:
                    scene.close()


@unittest.skipUnless(os.name == 'nt', 'Windows process handles')
class WindowsProcessProbeTests(unittest.TestCase):
    def test_current_process_is_alive_without_signals(self):
        with patch.object(transaction.os, 'kill', side_effect=AssertionError('must not send signals')):
            self.assertTrue(transaction._pid_alive(os.getpid()))
            for invalid in (0, -1, 0x100000000):
                self.assertFalse(transaction._pid_alive(invalid))

    def test_probe_preserves_live_child_and_recognizes_exit(self):
        child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read(1)'],
                                 stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            with patch.object(transaction.os, 'kill', side_effect=AssertionError('must not send signals')):
                self.assertTrue(transaction._pid_alive(child.pid))
                self.assertIsNone(child.poll(), 'checking a lock owner must not terminate it')
                child.communicate(b'x', timeout=10)
                self.assertEqual(child.returncode, 0)
                self.assertFalse(transaction._pid_alive(child.pid))
        finally:
            if child.poll() is None:
                child.kill()  # Only the worker created by this test.
                child.wait(timeout=10)
            if child.stdin:
                child.stdin.close()

    def test_unknown_state_keeps_lock_and_closes_handle(self):
        kernel32 = SimpleNamespace(OpenProcess=Mock(return_value=None),
                                   WaitForSingleObject=Mock(), CloseHandle=Mock())
        with patch('ctypes.WinDLL', return_value=kernel32), patch('ctypes.get_last_error') as error:
            for code, alive in ((87, False), (5, True), (8, True)):
                error.return_value = code
                self.assertEqual(transaction._pid_alive(1234), alive)
            kernel32.CloseHandle.assert_not_called()
            kernel32.OpenProcess.return_value = 0x100000001  # Preserve a 64-bit HANDLE.
            for result, alive in ((0, False), (258, True), (0xFFFFFFFF, True)):
                kernel32.WaitForSingleObject.return_value = result
                self.assertEqual(transaction._pid_alive(1234), alive)
                kernel32.WaitForSingleObject.assert_called_with(0x100000001, 0)
                kernel32.CloseHandle.assert_called_with(0x100000001)


if __name__ == "__main__":
    unittest.main(verbosity=2)
