"""Run acceptance phases in isolated Blender processes, abort on any failure."""

import argparse, json, os, subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
parser.add_argument("--gui", action="store_true")
parser.add_argument("--gui-only", action="store_true")
args = parser.parse_args()
env = os.environ.copy()
# Tests never install into or save the artist's preference directory.
env["BLENDER_USER_RESOURCES"] = str(root / ".blender-test" / "user")
if not args.gui_only:
    for test in sorted((root / "tests/integration").glob("phase*.py")) + [
        root / "tests/integration" / name
        for name in ("native_registration.py", "smart_masks.py", "projected_masks.py", "edge_wear_textures.py")
    ]:
        print(f"Running {test.name}", flush=True)
        subprocess.run(
            [
                args.blender,
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                str(test),
            ],
            cwd=root,
            env=env,
            check=True,
        )
    subprocess.run(
        [
            args.blender,
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python-expr",
            "import unittest; suite=unittest.defaultTestLoader.discover('tests/unit'); result=unittest.TextTestRunner(verbosity=2).run(suite); assert result.wasSuccessful()",
        ],
        cwd=root,
        env=env,
        check=True,
    )
if args.gui or args.gui_only:
    for filename, result_name in [
        ("native_ui.py", "native-ui.json"),
    ]:
        result_path = root / ".blender-test" / result_name
        result_path.unlink(missing_ok=True)
        subprocess.run(
            [
                args.blender,
                "--factory-startup",
                "--enable-event-simulate",
                "--python",
                str(root / "tests/integration" / filename),
                "--",
                "-ApplePersistenceIgnoreState",
                "YES",
            ],
            cwd=root,
            env=env,
            check=True,
        )
        result = result_path.read_text()
        report = json.loads(result)
        if report != {
            "native_regions": "PASS",
            "custom_handlers": 0,
            "native_asset_click": "PASS",
            "native_T_N": "PASS",
            "native_2d_3d": "PASS",
        }:
            raise RuntimeError(result)
print("ALL TESTS PASSED")
