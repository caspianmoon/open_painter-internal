"""Export failure safety (quality remedy M2B, plan §7): replacing an existing export survives a
failure at every stage, a crash between two replacements is recovered from the journal,
exclusive creation stays exclusive, and every export entry point is transactional.

Fault matrix (each injected on the real publication path, on a target package of maps,
manifest and mesh that replaces an earlier export):
- the second map fails while publishing;
- the manifest fails;
- the mesh exporter fails after the maps were generated;
- a later texture set fails to evaluate (request level);
- a crash between two replacements (a journal with backups left behind);
- the destination filesystem refuses the write (permission).
After each: the previous export is byte-identical, no staging or backup files remain.
"""
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.export import plan as planning, transaction
from open_painter.ui import export_view

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
project = scene.open_painter
REPORT = ROOT / ".blender-test/quality/export-safety.json"
REPORT.parent.mkdir(parents=True, exist_ok=True)
report = {"blender": bpy.app.version_string, "checks": []}


def check(name, **facts):
    report["checks"].append({"name": name, **facts})
    print(f"EXPORT_SAFETY {name}: PASS {facts}", flush=True)


def snapshot(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(folder).iterdir() if p.is_file()}


def leftovers(folder):
    return sorted(p.name for p in Path(folder).iterdir() if ".op-staging-" in p.name or ".op-backup-" in p.name
                  or p.name == transaction.JOURNAL)


seam, _ = fixtures.seam_object(scene, resolution=512)
plate, _ = fixtures.channel_plate(scene, resolution=512)
seam = next(t for t in project.texture_sets if t.name == "Seams")
plate = next(t for t in project.texture_sets if t.name == "Plate")
view = export_view.view_settings(bpy.context)
view.contents, view.scope, view.folder_source = "TARGET", "ALL", "PROJECT"
project.target, project.export_mesh = "UNREAL", True

with tempfile.TemporaryDirectory(prefix="op-export-safety-") as tmp:
    folder = Path(tmp) / "publish"
    project.export_directory = str(folder)

    def request(overwrite=True):
        plan = planning.resolve(bpy.context, contents="TARGET", scope="ALL", folder_source="PROJECT")
        return planning.execute(bpy.context, plan, overwrite=overwrite)

    first = request(overwrite=False)
    original = snapshot(folder)
    assert len(original) >= 6 and any(name.endswith(".fbx") for name in original), original
    assert not leftovers(folder)
    check("first export publishes maps, manifests and mesh", files=sorted(original))

    # Exclusive creation stays exclusive: without overwrite nothing is touched.
    refusal = None
    try:
        request(overwrite=False)
    except ValueError as error:
        refusal = str(error)
    assert refusal and "already exist" in refusal, refusal
    assert snapshot(folder) == original and not leftovers(folder)
    check("exclusive creation refused", message=refusal[:80])

    def attempt(label, inject, expect=Exception):
        """Run a replacing export under a fault; the originals must survive byte for byte."""
        outcome = None
        inject_ctx = inject()
        inject_ctx.__enter__()
        try:
            request(overwrite=True)
            outcome = "no error raised"
        except Exception as error:
            outcome = f"{type(error).__name__}: {error}"
        finally:
            inject_ctx.__exit__(None, None, None)
        after = snapshot(folder)
        lost = sorted(name for name, digest in original.items() if after.get(name) != digest)
        extra = sorted(set(after) - set(original))
        assert outcome != "no error raised", label
        assert not lost, (label, outcome, lost)
        assert not extra and not leftovers(folder), (label, extra, leftovers(folder))
        check(label, outcome=outcome[:120])

    class PublishFault:
        """Fail the n-th destination write of the transaction."""

        def __init__(self, nth, suffix=None):
            self.nth, self.suffix, self.count = nth, suffix, 0

        def __enter__(self):
            self.real = transaction._copy_flushed

            def failing(source, target):
                if self.suffix is None or str(target).split(".op-staging-")[0].endswith(self.suffix):
                    self.count += 1
                    if self.count == self.nth:
                        raise OSError(f"injected: disk full while publishing {Path(str(target).split('.op-staging-')[0]).name}")
                return self.real(source, target)
            transaction._copy_flushed = failing
            return self

        def __exit__(self, *_):
            transaction._copy_flushed = self.real

    attempt("failure publishing the second map", lambda: PublishFault(2, ".png"))
    attempt("failure publishing the manifest", lambda: PublishFault(1, "_manifest.json"))
    attempt("failure publishing the mesh", lambda: PublishFault(1, ".fbx"))

    class MeshFault:
        def __enter__(self):
            from open_painter.ui import target_panel
            self.module, self.real = target_panel, target_panel._export_mesh

            def failing(*args, **kwargs):
                raise RuntimeError("injected: FBX exporter failed")
            target_panel._export_mesh = failing
            return self

        def __exit__(self, *_):
            self.module._export_mesh = self.real

    attempt("mesh export failure after the maps", MeshFault)

    class SetFault:
        def __enter__(self):
            from open_painter.export import exporter
            self.module, self.real = exporter, exporter.generate

            def failing(context, ts, settings, **kwargs):
                if ts.name == "Plate":
                    raise RuntimeError("injected: the second set failed to evaluate")
                return self.real(context, ts, settings, **kwargs)
            planning_generate = failing
            exporter.generate = failing
            return self

        def __exit__(self, *_):
            self.module.generate = self.real

    attempt("a later texture set failing", SetFault)

    # A crash between two replacements: simulate by stopping the transaction after the backups
    # were made and one destination written, leaving the journal behind; the next request recovers.
    class Crash(BaseException):
        pass

    class CrashFault:
        def __init__(self):
            self.count = 0

        def __enter__(self):
            self.real_replace = os.replace

            def replacing(src, dst):
                self.real_replace(src, dst)
                if ".op-staging-" in str(src):
                    self.count += 1
                    if self.count == 2:
                        raise Crash()
            self.patched = replacing
            transaction.os.replace = replacing
            return self

        def __exit__(self, *_):
            transaction.os.replace = self.real_replace

    crash = CrashFault()
    crash.__enter__()
    try:
        # The rollback runs on BaseException too; to leave a crash scene behind, disable it.
        real_commit_cleanup = transaction.Transaction.commit

        def commit_without_rollback(self, overwrite=False):
            self.validate(overwrite)
            for folder_ in self.journals():
                transaction.recover(folder_)
            for item in self.items:
                item.staging = item.destination.with_name(f'{item.destination.name}.op-staging-{self.identifier}')
                if item.destination.exists():
                    item.backup = item.destination.with_name(f'{item.destination.name}.op-backup-{self.identifier}')
            for folder_, items in self.journals().items():
                (folder_ / transaction.JOURNAL).write_text(json.dumps({'transaction': self.identifier, 'items': [i.record() for i in items]}))
            for item in self.items:
                if item.backup is not None:
                    transaction.os.replace(item.destination, item.backup)
            for item in self.items:
                transaction._copy_flushed(item.source, item.staging)
                transaction.os.replace(item.staging, item.destination)     # the injected crash fires here
            raise AssertionError("the crash did not fire")
        transaction.Transaction.commit = commit_without_rollback
        try:
            request(overwrite=True)
        except Crash:
            pass
        finally:
            transaction.Transaction.commit = real_commit_cleanup
    finally:
        crash.__exit__()
    left = leftovers(folder)
    assert transaction.JOURNAL in left and any(".op-backup-" in name for name in left), left
    after_crash = snapshot(folder)
    assert any(after_crash.get(name) != digest for name, digest in original.items()), "the crash scene should be partial"
    restored = transaction.recover(folder)
    assert snapshot(folder) == original and not leftovers(folder), (restored, leftovers(folder))
    check("a crash between replacements is recovered from the journal", restored=restored)
    # Recovery also runs at the start of the next request: leave a journal and export again.
    crash2 = CrashFault(); crash2.__enter__()
    transaction.Transaction.commit = commit_without_rollback
    try:
        request(overwrite=True)
    except Crash:
        pass
    finally:
        transaction.Transaction.commit = real_commit_cleanup
        crash2.__exit__()
    assert leftovers(folder)
    request(overwrite=True)
    assert not leftovers(folder) and set(snapshot(folder)) == set(original)
    check("the next request recovers a crashed one first")
    # The maps are byte-identical across requests; the FBX carries a creation timestamp.
    fresh = snapshot(folder)
    assert all(fresh[name] == digest for name, digest in original.items() if name.endswith(".png")), "maps differ between requests"
    original = fresh

    # A destination the filesystem refuses (read-only folder): nothing changes, a clear error.
    if os.geteuid() != 0:
        mode = folder.stat().st_mode
        os.chmod(folder, stat.S_IRUSR | stat.S_IXUSR)
        try:
            attempt("read-only destination folder", lambda: PublishFault(10 ** 6))
        finally:
            os.chmod(folder, mode)
    # The destination changes after the preflight: a file that appeared since the plan was
    # resolved is not overwritten without the replacing policy, and stays intact.
    fresh = Path(tmp) / "late"
    project.export_directory = str(fresh)
    plan = planning.resolve(bpy.context, contents="TARGET", scope="ALL", folder_source="PROJECT")
    assert not plan.conflicts
    fresh.mkdir()
    intruder = plan.files[1]
    intruder.write_bytes(b"someone else's file")
    refused = None
    try:
        planning.execute(bpy.context, plan, overwrite=False)
    except ValueError as error:
        refused = str(error)
    assert refused and "already exist" in refused, refused
    assert intruder.read_bytes() == b"someone else's file"
    assert sorted(p.name for p in fresh.iterdir()) == [intruder.name], sorted(p.name for p in fresh.iterdir())
    check("a file that appeared after the preflight is not overwritten", file=intruder.name)
    project.export_directory = str(folder)
    # The single-set route and the target button share the transaction.
    from open_painter.export.exporter import export
    seam = next(t for t in project.texture_sets if t.name == "Seams")
    single = Path(tmp) / "single"
    paths = export(bpy.context, seam, seam.export, directory=single)
    before_single = snapshot(single)
    fault = PublishFault(2, ".png"); fault.__enter__()
    try:
        export(bpy.context, seam, seam.export, directory=single, overwrite=True)
        raise AssertionError("expected the injected failure")
    except OSError:
        pass
    finally:
        fault.__exit__()
    assert snapshot(single) == before_single and not leftovers(single)
    check("the plain texture export is transactional too")

REPORT.write_text(json.dumps(report, indent=2))
print("EXPORT_SAFETY_PASS", REPORT, flush=True)
