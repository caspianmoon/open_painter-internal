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

    # Hard process terminations at the publication boundaries, on the real commit path: a
    # background Blender runs the same export (with padding None, so its bytes differ from the
    # published Automatic export) and is killed by the failpoint; recovery runs here.
    import subprocess
    blender = Path(bpy.app.binary_path)

    def crash_export(failpoint, padding_mode="NONE"):
        env = dict(os.environ, OP_EXPORT_FAILPOINT=failpoint, OP_WORKER_FOLDER=str(folder), OP_WORKER_PADDING=padding_mode)
        env.pop(transaction.FAULT_VARIABLE, None)
        completed = subprocess.run([str(blender), "--background", "--factory-startup", "--python",
                                    str(ROOT / "tests/integration/export_safety_worker.py")], env=env, capture_output=True, text=True)
        assert completed.returncode == 3, (failpoint, completed.returncode, completed.stdout[-800:], completed.stderr[-800:])
        return completed

    # The bytes a padding-None export publishes (for the post-commit expectation).
    none_folder = Path(tmp) / "none"
    project.export_directory = str(none_folder)
    for ts in project.texture_sets:
        ts.export.padding_mode = "NONE"
    request(overwrite=False)
    none_maps = {name: digest for name, digest in snapshot(none_folder).items() if name.endswith(".png")}
    for ts in project.texture_sets:
        ts.export.padding_mode = "AUTOMATIC"
    project.export_directory = str(folder)
    original_maps = {name: digest for name, digest in original.items() if name.endswith(".png")}
    assert none_maps != original_maps, "the two padding modes must produce different bytes"

    for failpoint, expected_maps, label in (("after_backups", original_maps, "before the decision"),
                                            ("after_first_publish", original_maps, "mid-publication"),
                                            ("before_commit_decision", original_maps, "just before the decision"),
                                            ("after_commit_decision", none_maps, "just after the decision"),
                                            ("after_first_backup_removed", none_maps, "during cleanup")):
        crash_export(failpoint)
        left = leftovers(folder)
        assert transaction.JOURNAL in left, (failpoint, left)
        restored = transaction.recover(folder)
        maps = {name: digest for name, digest in snapshot(folder).items() if name.endswith(".png")}
        assert maps == expected_maps, (failpoint, label)
        assert not leftovers(folder), (failpoint, leftovers(folder))
        assert transaction.recover(folder) == []
        check(f"hard termination {label} ({failpoint}) recovers to one coherent state", actions=restored[:3])
        if expected_maps is none_maps:
            # Put the Automatic export back for the next case.
            request(overwrite=True)
            assert {n: d for n, d in snapshot(folder).items() if n.endswith(".png")} == original_maps
    original = snapshot(folder)

    # Recovery also runs at the start of the next request: leave a crashed journal, export again.
    crash_export("after_backups")
    assert leftovers(folder)
    request(overwrite=True)
    assert not leftovers(folder) and set(snapshot(folder)) == set(original)
    check("the next request recovers a crashed one first")

    # A cleanup failure after the decision keeps every new file and says so.
    os.environ[transaction.FAULT_VARIABLE] = "cleanup"
    try:
        plan = planning.resolve(bpy.context, contents="TARGET", scope="ALL", folder_source="PROJECT")
        planning.execute(bpy.context, plan, overwrite=True)
    finally:
        os.environ.pop(transaction.FAULT_VARIABLE, None)
    assert plan.outcome.cleanup_pending and "cleanup pending" in plan.outcome.message, plan.outcome
    assert {n: d for n, d in snapshot(folder).items() if n.endswith(".png")} == original_maps
    transaction.recover(folder)
    assert not leftovers(folder)
    check("post-commit cleanup failure keeps the complete export and reports cleanup pending")

    # Another process publishing into the folder: this one refuses instead of racing it.
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, sys.argv[1]); from open_painter.export import transaction;"
        "transaction._Lock(sys.argv[2]).acquire(); print('held', flush=True); time.sleep(60)"),
        str(ROOT / "src"), str(folder)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        refused = None
        try:
            request(overwrite=True)
        except transaction.ExportBusy as error:
            refused = str(error)
        assert refused, "a second export into a locked folder must be refused"
        assert {n: d for n, d in snapshot(folder).items() if n.endswith(".png")} == original_maps
    finally:
        holder.kill(); holder.wait()
    request(overwrite=True)       # the dead holder's lock is stale
    assert not leftovers(folder)
    check("an overlapping export is refused while the folder is locked", message=refused[:80])
    # The maps are byte-identical across requests; the FBX carries a creation timestamp.
    fresh = snapshot(folder)
    assert all(fresh[name] == digest for name, digest in original.items() if name.endswith(".png")), "maps differ between requests"
    original = fresh

    # A destination the filesystem refuses (read-only folder): nothing changes, a clear error.
    # POSIX: a folder without write permission (root ignores it). Windows: a folder's read-only
    # attribute stops nothing, so an ACL denies this user adding and removing its entries.
    if sys.platform == "win32":
        system = Path(os.environ["SystemRoot"]) / "System32"
        sid = subprocess.run([str(system / "whoami.exe"), "/user", "/fo", "csv", "/nh"], capture_output=True,
                             text=True, check=True).stdout.strip().split(",")[-1].strip('"')
        subprocess.run([str(system / "icacls.exe"), str(folder), "/deny", f"*{sid}:(WD,AD,DC)"],
                       capture_output=True, check=True)
        try:
            attempt("read-only destination folder", lambda: PublishFault(10 ** 6))
        finally:
            subprocess.run([str(system / "icacls.exe"), str(folder), "/remove:d", f"*{sid}"],
                           capture_output=True, check=True)
    elif os.geteuid() != 0:
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
