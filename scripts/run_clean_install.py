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
import platform
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "blender_manifest.toml").read_text(encoding="utf-8"))["version"]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
parser.add_argument("--package", type=Path, default=root / "dist" / f"open_painter-{version}.zip")
parser.add_argument("--keep", action="store_true", help="Keep the profile and work folders")
parser.add_argument("--gpu-backend", choices=("metal", "opengl", "vulkan"),
                    help="Blender's --gpu-backend in both phases (default: the factory setting)")
# A window, EEVEE and a Cycles bake: 4.6 GiB on an 18 GB Mac (docs/native/deep-stacks.md).
parser.add_argument("--cap-gb", type=float, default=6.0,
                    help="Kill a Blender process whose memory footprint passes this (scripts/run_guarded.py)")
args = parser.parse_args()
if not args.package.is_file():
    sys.exit(f"Package missing: {args.package}")
profile = Path(tempfile.mkdtemp(prefix="op-clean-profile-"))
work = Path(tempfile.mkdtemp(prefix="op-clean-install-"))
env = dict(os.environ, BLENDER_USER_RESOURCES=str(profile), OP_CLEAN_INSTALL_ZIP=str(args.package.resolve()),
           OP_CLEAN_INSTALL_DIR=str(work))
env.pop("PYTHONPATH", None)
# As in blender_test_profile: Cycles on Metal keeps its base path-state count, or a cube's bake
# passes the memory cap on a large Mac.
env.setdefault("CYCLES_METAL_GROW_PERCENT", "0")
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
    # Every Blender launch goes through the memory guard, one at a time.
    guard = [sys.executable, str(root / "scripts/run_guarded.py"), "--cap-gb", str(args.cap_gb),
             "--start-gb", "3", "--wait-seconds", "120", "--"]
    backend = ["--gpu-backend", args.gpu_backend] if args.gpu_backend else []
    completed = subprocess.run([*guard, args.blender, *(["--factory-startup"] if phase == 1 else []), *backend,
                                "--enable-event-simulate", "--python",
                                str(root / "tests/integration/clean_install_ui.py"),
                                *(["--", "-ApplePersistenceIgnoreState", "YES"] if sys.platform == "darwin" else [])],
                               cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in completed.stdout.splitlines():
        if line.startswith(("GUARD_SUMMARY", "GUARD_KILLED")):
            print(f"phase {phase}: {line}")
    report_path = work / f"clean-install-phase{phase}.json"
    if not report_path.is_file():
        print(completed.stdout[-3000:], completed.stderr[-3000:])
        sys.exit(f"Phase {phase} wrote no report")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    reports[phase] = report
    print(f"phase {phase}: {report['status']} · {len(report['steps'])} steps")
    for step in report["steps"]:
        print("   ", step)
    if report["status"] != "PASS":
        print("\n".join(report["errors"]))
        sys.exit(f"Phase {phase} failed (profile {profile}, work {work})")
summary = {"package": str(args.package), "version": version, "blender": reports[1]["blender"],
           "platform": sys.platform, "machine": platform.machine(), "profile": str(profile), "work": str(work),
           "phases": reports}
(root / ".blender-test").mkdir(exist_ok=True)
(root / ".blender-test/clean-install.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print("CLEAN_INSTALL_PASS", version, reports[1]["blender"], sys.platform, platform.machine())
if not args.keep:
    import shutil
    shutil.rmtree(profile, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
