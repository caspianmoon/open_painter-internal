"""Engine validation runner (quality remedy, Phase 3): build the fixtures with the candidate
add-on, then run the Unreal and Unity validation scripts when an editor is available.

    python scripts/engine_validation.py [--blender <path>] [--out <folder>]

Editors are found through OP_UNREAL_EDITOR (UnrealEditor-Cmd binary), OP_UNREAL_PROJECT
(.uproject), OP_UNITY_EDITOR (Unity binary) and OP_UNITY_PROJECT (a URP project folder
with engine_validation/unity/Editor copied into Assets/). Without them the engine is
reported NOT RUN: nothing is purchased, deployed or faked.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
parser.add_argument("--out", type=Path, default=root / ".blender-test/quality/engine-fixtures")
parser.add_argument("--skip-fixtures", action="store_true")
args = parser.parse_args()
env = dict(os.environ, BLENDER_USER_RESOURCES=str(root / ".blender-test/user"))
status = {"fixtures": None, "unreal": {"status": "NOT RUN"}, "unity_urp": {"status": "NOT RUN"}}

if not args.skip_fixtures:
    subprocess.run([args.blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python",
                    str(root / "engine_validation/build_fixtures.py"), "--", str(args.out)], cwd=root, env=env, check=True)
record = json.loads((args.out / "fixtures.json").read_text(encoding="utf-8"))
status["fixtures"] = {"path": str(args.out / "fixtures.json"), "package": record.get("package"), "sha256": record.get("sha256"),
                      "commit": record.get("commit"), "blender": record.get("blender")}

unreal_editor, unreal_project = os.environ.get("OP_UNREAL_EDITOR"), os.environ.get("OP_UNREAL_PROJECT")
if unreal_editor and unreal_project and Path(unreal_editor).exists() and Path(unreal_project).exists():
    report = args.out / "unreal-validation.json"
    result = subprocess.run([unreal_editor, unreal_project, "-run=pythonscript",
                             f"-script={root / 'engine_validation/unreal/import_and_validate.py'}", "-unattended", "-nopause"],
                            env=dict(env, OP_FIXTURES=str(args.out / "unreal"), OP_REPORT=str(report)))
    status["unreal"] = {"status": json.loads(report.read_text(encoding="utf-8"))["status"] if report.exists() else "FAIL",
                        "exit": result.returncode, "report": str(report)}
else:
    status["unreal"]["reason"] = "no Unreal Editor on this machine (set OP_UNREAL_EDITOR and OP_UNREAL_PROJECT)"

unity_editor, unity_project = os.environ.get("OP_UNITY_EDITOR"), os.environ.get("OP_UNITY_PROJECT")
if unity_editor and unity_project and Path(unity_editor).exists() and Path(unity_project).exists():
    target = Path(unity_project) / "Assets/OpenPainterValidation/Editor"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(root / "engine_validation/unity/Editor/OpenPainterValidation.cs", target)
    report = args.out / "unity-validation.json"
    result = subprocess.run([unity_editor, "-batchmode", "-nographics", "-projectPath", unity_project, "-executeMethod",
                             "OpenPainter.Validation.Run", "-opFixtures", str(args.out / "unity_urp"), "-opReport", str(report),
                             "-quit", "-logFile", str(args.out / "unity.log")])
    status["unity_urp"] = {"status": json.loads(report.read_text(encoding="utf-8"))["status"] if report.exists() else "FAIL",
                           "exit": result.returncode, "report": str(report)}
else:
    status["unity_urp"]["reason"] = "no Unity Editor on this machine (set OP_UNITY_EDITOR and OP_UNITY_PROJECT)"

(args.out / "engine-validation-status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
print(json.dumps(status, indent=2))
sys.exit(0)
