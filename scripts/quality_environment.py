"""Record the reference environment of the quality remedy runs (plan §4, Phase 0).

    blender --factory-startup --python scripts/quality_environment.py [-- --package dist/x.zip]

Writes .blender-test/quality/environment.json: commit, candidate package path and sha256,
Blender build, OS, CPU, GPU and backend, RAM, display size and UI scale, the fixture
resolutions and layer counts the runs use. Works in background too (GPU fields then say so).
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import bpy

root = Path(__file__).resolve().parents[1]
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
package = Path(argv[argv.index("--package") + 1]) if "--package" in argv else None
out = root / ".blender-test/quality/environment.json"
out.parent.mkdir(parents=True, exist_ok=True)


def run(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


record = {
    "commit": run(["git", "-C", str(root), "rev-parse", "HEAD"]),
    "branch": run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"]),
    "package": {"path": str(package) if package else None,
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest() if package and package.exists() else None},
    "blender": {"version": bpy.app.version_string, "hash": bpy.app.build_hash.decode() if isinstance(bpy.app.build_hash, bytes) else str(bpy.app.build_hash),
                "build_date": str(bpy.app.build_date), "background": bpy.app.background},
    "os": {"platform": platform.platform(), "release": platform.mac_ver()[0] or platform.release(), "machine": platform.machine()},
    "cpu": {"brand": run(["sysctl", "-n", "machdep.cpu.brand_string"]) or platform.processor(),
            "cores": os.cpu_count()},
    "ram_gb": round(int(run(["sysctl", "-n", "hw.memsize"]) or 0) / 2 ** 30, 1),
    "python": sys.version.split()[0],
    "numpy": __import__("numpy").__version__,
    "fixtures": {"production_prop": {"resolution": 2048, "layers": 25, "sets": 3},
                 "stress": {"resolution": 4096, "layers": 8}, "session": {"strokes": 500, "resolution": 2048}},
}
try:
    import gpu
    record["gpu"] = {"backend": gpu.platform.backend_type_get(), "renderer": gpu.platform.renderer_get(),
                     "vendor": gpu.platform.vendor_get(), "version": gpu.platform.version_get()}
except Exception as error:   # background: no GPU context
    record["gpu"] = {"note": f"not available in this session ({error})"}
if not bpy.app.background:
    window = bpy.context.window_manager.windows[0]
    record["display"] = {"window_px": [window.width, window.height],
                         "ui_scale": bpy.context.preferences.view.ui_scale,
                         "resolution_scale": bpy.context.preferences.system.ui_scale}
else:
    record["display"] = {"note": "background session; the GUI probes record their window size in their reports"}
out.write_text(json.dumps(record, indent=2), encoding="utf-8")
print("QUALITY_ENVIRONMENT", out, flush=True)
if not bpy.app.background:
    bpy.ops.wm.quit_blender()
