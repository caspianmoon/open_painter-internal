"""Clean-install launch test: the packaged extension in a fresh Blender profile, two processes.

    python3 scripts/run_clean_install.py [--package dist/open_painter-<version>.zip] [--blender PATH]

Phase 1 installs the ZIP into an empty profile and works through set-up, material, controls,
layers, mask, bake, paint and save; phase 2 starts a new Blender, reopens the file, exports for
Blender and Unreal, disables and re-enables the extension and opens the project again
(tests/integration/clean_install_ui.py). The source checkout is never on the path.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "blender_manifest.toml").read_text())["version"]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
parser.add_argument("--package", type=Path, default=root / "dist" / f"open_painter-{version}.zip")
parser.add_argument("--keep", action="store_true", help="Keep the profile and work folders")
args = parser.parse_args()
if not args.package.is_file():
    sys.exit(f"Package missing: {args.package}")
profile = Path(tempfile.mkdtemp(prefix="op-clean-profile-"))
work = Path(tempfile.mkdtemp(prefix="op-clean-install-"))
env = dict(os.environ, BLENDER_USER_RESOURCES=str(profile), OP_CLEAN_INSTALL_ZIP=str(args.package.resolve()),
           OP_CLEAN_INSTALL_DIR=str(work))
env.pop("PYTHONPATH", None)
# The upgrade session (phase 2, step 18) opens a file saved by the 0.12.2 build when the
# compatibility fixture exists; the report says SKIPPED otherwise.
legacy = root / ".blender-test/quality/compat-0.12.2.blend"
if legacy.is_file():
    import shutil
    shutil.copy(legacy, work / legacy.name)
reports = {}
for phase in (1, 2):
    env["OP_CLEAN_INSTALL_PHASE"] = str(phase)
    # Phase 1 starts from factory settings in the empty profile; phase 2 starts the way a user
    # does, with the preferences that phase 1 saved (the extension enabled).
    completed = subprocess.run([args.blender, *(["--factory-startup"] if phase == 1 else []),
                                "--enable-event-simulate", "--python",
                                str(root / "tests/integration/clean_install_ui.py"),
                                *(["--", "-ApplePersistenceIgnoreState", "YES"] if sys.platform == "darwin" else [])],
                               cwd=work, env=env, capture_output=True, text=True)
    report_path = work / f"clean-install-phase{phase}.json"
    if not report_path.is_file():
        print(completed.stdout[-3000:], completed.stderr[-3000:])
        sys.exit(f"Phase {phase} wrote no report")
    report = json.loads(report_path.read_text())
    reports[phase] = report
    print(f"phase {phase}: {report['status']} · {len(report['steps'])} steps")
    for step in report["steps"]:
        print("   ", step)
    if report["status"] != "PASS":
        print("\n".join(report["errors"]))
        sys.exit(f"Phase {phase} failed (profile {profile}, work {work})")
summary = {"package": str(args.package), "version": version, "blender": reports[1]["blender"],
           "platform": sys.platform, "machine": os.uname().machine, "profile": str(profile), "work": str(work),
           "phases": reports}
(root / ".blender-test").mkdir(exist_ok=True)
(root / ".blender-test/clean-install.json").write_text(json.dumps(summary, indent=2))
print("CLEAN_INSTALL_PASS", version, reports[1]["blender"], sys.platform, os.uname().machine)
if not args.keep:
    import shutil
    shutil.rmtree(profile, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
