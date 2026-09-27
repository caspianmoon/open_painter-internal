"""Utility-map bake and geometry rasterisation timings on the artist's 2K project.

Usage: blender --background --factory-startup --python-exit-code 1 \
    --python tests/performance/bake_maps.py -- [--kinds POSITION WORLD_NORMAL ...] [--report PATH]
Outputs are hashed so two revisions can be compared for parity.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from time import perf_counter

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.baking.maps import utility, curvature
from open_painter.core import image_manager as images
from open_painter.painting.projection import geometry

DEFAULT_KINDS = ("POSITION", "WORLD_NORMAL", "MATERIAL_ID", "UV_ISLAND", "UV_BORDER", "SELECTION", "THICKNESS")


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--kinds", nargs="*", default=list(DEFAULT_KINDS))
    parser.add_argument("--curvature", action="store_true", help="Also time bake_curvature using the texture set's selected edge method")
    parser.add_argument("--geometry", type=int, default=2048, help="Triplanar geometry raster size (0 to skip)")
    parser.add_argument("--report", type=Path, default=ROOT / ".blender-test" / "bake-maps.json")
    return parser.parse_args(argv)


def digest(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()[:16]


def run():
    args = parse()
    source = ROOT / "open painter test.blend"
    folder = ROOT / ".blender-test" / "bake-maps"
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / "bake-maps.blend"
    shutil.copy2(source, copy)
    open_painter.register()
    bpy.ops.wm.open_mainfile(filepath=str(copy))
    scene = bpy.context.scene
    ts = scene.open_painter.texture_sets[scene.open_painter.active_texture_set]
    report = {"blender": bpy.app.version_string, "resolution": int(ts.resolution),
              "polygons": sum(len(o.data.polygons) for o in scene.objects if o.type == "MESH"),
              "thickness_resolution": int(ts.bake.thickness_resolution), "thickness_samples": int(ts.bake.thickness_samples),
              "steps": []}

    def record(label, function):
        started = perf_counter()
        value = function()
        elapsed = (perf_counter() - started) * 1000
        pixels = images.pixels(value) if isinstance(value, bpy.types.Image) else value
        entry = {"step": label, "ms": round(elapsed, 1), "sha256": digest(pixels),
                 "coverage": float(np.mean(pixels[..., 3] > 0)) if pixels.ndim == 3 and pixels.shape[-1] == 4 else None}
        report["steps"].append(entry)
        print("BAKE_STEP", json.dumps(entry), flush=True)

    if args.geometry:
        record(f"geometry {args.geometry}", lambda: np.concatenate(geometry(ts, args.geometry)[:2], axis=-1))
    for kind in args.kinds:
        record(kind, lambda kind=kind: utility.bake_utility(ts, kind))
    if args.curvature:
        record("CURVATURE", lambda: curvature.bake_curvature(ts))
    args.report.write_text(json.dumps(report, indent=1))
    print("BAKE_MAPS_SUMMARY", json.dumps({s["step"]: s["ms"] for s in report["steps"]}), flush=True)


run()
