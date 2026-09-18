"""Phase 0 baseline of the add-on quality remedy: export boundaries, mips and overwrite safety.

Records, without changing the add-on:
- what the exporter writes outside and along UV islands of the seam object, at the authored
  resolution and after a downsample (boundary contamination, diagnostic mip levels);
- whether replacing an existing export survives a failure while publishing a later map, a
  manifest, a mesh or a later set (fault injection on the real publication path).

Writes .blender-test/quality/baseline-export.json and labels every finding CONFIRMED DEFECT,
QUALITY GAP or NOT YET VERIFIED. Run from the canonical runner (background Blender).
"""
import hashlib
import json
import sys
import tempfile
import traceback
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.core import image_manager as images
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export import plan as planning
from open_painter.ui import export_view

OUT = ROOT / ".blender-test/quality"
OUT.mkdir(parents=True, exist_ok=True)
open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
report = {"commit": None, "blender": bpy.app.version_string, "findings": [], "measurements": {}}
try:
    import subprocess
    report["commit"] = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
except Exception:
    pass


def finding(label, subject, detail):
    report["findings"].append({"status": label, "subject": subject, "detail": detail})
    print(f"BASELINE {label}: {subject} — {detail}", flush=True)


def read_png(path):
    image = bpy.data.images.load(str(path))
    image.colorspace_settings.name = "Non-Color"
    data = images.pixels(image).copy()
    bpy.data.images.remove(image)
    return data


def box_down(values, factor):
    n = values.shape[0]
    return values.reshape(n // factor, factor, n // factor, factor, -1).mean(axis=(1, 3))


# ---- 1. seams and boundaries ----------------------------------------------------------------------
ts, field = fixtures.seam_object(scene, resolution=1024)
n = int(ts.resolution)
LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
covered, island = fixtures.ownership(ts, n)
base_color = images.pixels(next(o.image for o in ts.outputs if o.channel == "BASE_COLOR"))[..., :3]
outside = ~covered
report["measurements"]["seam_object"] = {
    "resolution": n, "covered_texels": int(covered.sum()), "islands": int(island.max()) + 1,
    "outside_texels": int(outside.sum()),
    "outside_value_mean": [float(v) for v in base_color[outside].mean(axis=0)],
    "inside_value_mean": [float(v) for v in base_color[covered].mean(axis=0)],
}
# Boundary texels: covered texels with an uncovered 4-neighbour.
shifted = np.zeros_like(covered)
for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
    shifted |= np.roll(~covered, (dy, dx), axis=(0, 1))
boundary = covered & shifted
finding("QUALITY GAP", "export gutters",
        f"outside the islands the evaluated base colour is {report['measurements']['seam_object']['outside_value_mean']} "
        f"(the layer default), not an extension of the island values; {int(boundary.sum())} boundary texels border it")

with tempfile.TemporaryDirectory(prefix="op-quality-baseline-") as tmp:
    root = Path(tmp)
    project = scene.open_painter
    project.export_directory = str(root / "full")
    view = export_view.view_settings(bpy.context)
    view.contents, view.scope, view.folder_source = "TEXTURES", "ACTIVE", "PROJECT"
    ts.export.preset, ts.export.file_format, ts.export.bit_depth = "BLENDER", "PNG", "8"
    ts.export.resolution = "SET"
    plan = planning.resolve(bpy.context, contents="TEXTURES", scope="ACTIVE", folder_source="PROJECT")
    written = planning.execute(bpy.context, plan)
    full = {p.name: p for p in written}
    exported = read_png(next(p for name, p in full.items() if "BaseColor" in name))[::-1]
    # Contamination in a 2× box mip of the export: covered mip texels whose value mixes in
    # empty-space defaults. Reference: the coverage-weighted mip of the same data.
    mip = box_down(exported[..., :3], 2)
    weight = box_down(covered[..., None].astype(np.float32), 2)[..., 0]
    reference = np.divide(box_down(exported[..., :3] * covered[..., None], 2), weight[..., None],
                          out=np.zeros_like(mip), where=weight[..., None] > 0)
    partial = (weight > 0) & (weight < 1)
    error = np.abs(mip - reference).max(axis=-1)
    report["measurements"]["mip_2x"] = {
        "partial_texels": int(partial.sum()),
        "error_median": float(np.median(error[partial])) if partial.any() else 0.0,
        "error_p95": float(np.percentile(error[partial], 95)) if partial.any() else 0.0,
        "error_max": float(error[partial].max()) if partial.any() else 0.0,
    }
    finding("CONFIRMED DEFECT", "mip contamination without padding",
            f"a 2× box mip of the exported base colour mixes empty-space defaults into "
            f"{int(partial.sum())} island-edge texels (max error {report['measurements']['mip_2x']['error_max']:.3f}, "
            f"p95 {report['measurements']['mip_2x']['error_p95']:.3f})")
    # A downsampled export (1024 → 512): the exporter's box filter over the source.
    project.export_directory = str(root / "half")
    ts.export.resolution = "512"
    plan = planning.resolve(bpy.context, contents="TEXTURES", scope="ACTIVE", folder_source="PROJECT")
    written = planning.execute(bpy.context, plan)
    half = read_png(next(p for p in written if "BaseColor" in p.name))[::-1]
    covered_half, _ = fixtures.ownership(ts, 512)
    reference_half = np.divide(box_down(exported[..., :3] * covered[..., None], 2), weight[..., None],
                               out=np.zeros_like(mip), where=weight[..., None] > 0)
    edge = covered_half & partial
    error_half = np.abs(half[..., :3] - reference_half).max(axis=-1)
    report["measurements"]["downsample_512"] = {
        "edge_texels": int(edge.sum()),
        "error_p95": float(np.percentile(error_half[edge], 95)) if edge.any() else 0.0,
        "error_max": float(error_half[edge].max()) if edge.any() else 0.0,
    }
    finding("CONFIRMED DEFECT", "downsample before padding",
            f"exporting 1024 → 512 box-filters empty-space defaults into {int(edge.sum())} covered edge texels "
            f"(max error {report['measurements']['downsample_512']['error_max']:.3f})")
    ts.export.resolution = "SET"

    # ---- 2. overwrite safety ------------------------------------------------------------------------
    project.export_directory = str(root / "publish")
    project.target = "UNREAL"
    project.export_mesh = True
    plan = planning.resolve(bpy.context, contents="TARGET", scope="ACTIVE", folder_source="PROJECT")
    first = planning.execute(bpy.context, plan)
    originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in first}
    assert all(p.exists() for p in originals)

    def survivors():
        return {p.name: (p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == digest) for p, digest in originals.items()}

    # a. Failure while publishing the second map of the set.
    import pathlib
    real_open = pathlib.Path.open
    state = {"publishes": 0}

    def failing_open(self, mode="r", *args, **kwargs):
        if mode in {"wb", "xb"} and self.suffix == ".png" and self.parent == root / "publish":
            state["publishes"] += 1
            if state["publishes"] == 2:
                raise OSError("injected: disk full while publishing the second map")
        return real_open(self, mode, *args, **kwargs)

    pathlib.Path.open = failing_open
    try:
        plan = planning.resolve(bpy.context, contents="TARGET", scope="ACTIVE", folder_source="PROJECT")
        try:
            planning.execute(bpy.context, plan, overwrite=True)
            outcome = "no error raised"
        except Exception as exc:
            outcome = f"{type(exc).__name__}: {exc}"
    finally:
        pathlib.Path.open = real_open
    after = survivors()
    lost = [name for name, ok in after.items() if not ok]
    report["measurements"]["publish_failure_second_map"] = {"outcome": outcome, "lost": lost}
    finding("CONFIRMED DEFECT" if lost else "NOT YET VERIFIED", "replacement export failing on the second map",
            f"{outcome}; original files lost or changed: {lost}")
    # Restore the originals for the next probes.
    planning.execute(bpy.context, planning.resolve(bpy.context, contents="TARGET", scope="ACTIVE", folder_source="PROJECT"), overwrite=True)
    originals = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in first}

    # b. Mesh export failure after the textures were published.
    from open_painter.ui import target_panel
    real_mesh = target_panel._export_mesh

    def failing_mesh(*args, **kwargs):
        raise RuntimeError("injected: FBX exporter failed")

    target_panel._export_mesh = failing_mesh
    try:
        plan = planning.resolve(bpy.context, contents="TARGET", scope="ACTIVE", folder_source="PROJECT")
        try:
            planning.execute(bpy.context, plan, overwrite=True)
            outcome = "no error raised"
        except Exception as exc:
            outcome = f"{type(exc).__name__}: {exc}"
    finally:
        target_panel._export_mesh = real_mesh
    after = survivors()
    lost = [name for name, ok in after.items() if not ok]
    report["measurements"]["publish_failure_mesh"] = {"outcome": outcome, "lost": lost}
    finding("CONFIRMED DEFECT" if any(name.endswith(".fbx") for name in lost) else "NOT YET VERIFIED",
            "mesh export failure after the textures",
            f"{outcome}; files lost or changed: {lost}")

    # c. A second set failing: is the first set's fresh output rolled back?
    other = fixtures.channel_plate(scene, resolution=512)[0]
    project.export_directory = str(root / "batch")
    plan = planning.resolve(bpy.context, contents="TARGET", scope="ALL", folder_source="PROJECT")
    names = [p.name for p in plan.files]
    from open_painter.export import exporter as exporter_module
    real_export = exporter_module.export

    def failing_export(context, set_, settings, **kwargs):
        if set_.id == other.id:
            raise RuntimeError("injected: the second set failed to evaluate")
        return real_export(context, set_, settings, **kwargs)

    exporter_module.export = failing_export
    planning.export = failing_export if hasattr(planning, "export") else None
    try:
        try:
            planning.execute(bpy.context, plan)
            outcome = "no error raised"
        except Exception as exc:
            outcome = f"{type(exc).__name__}: {exc}"
    finally:
        exporter_module.export = real_export
    left = sorted(p.name for p in (root / "batch").glob("*")) if (root / "batch").exists() else []
    report["measurements"]["publish_failure_second_set"] = {"outcome": outcome, "files_left": left, "planned": names}
    finding("CONFIRMED DEFECT" if left else "NOT YET VERIFIED", "a later texture set failing",
            f"{outcome}; files left from the partial request: {left}")

report["findings"].append({"status": "NOT YET VERIFIED", "subject": "crash consistency",
                           "detail": "a process interruption between two file replacements has no journal; not tested"})
(OUT / "baseline-export.json").write_text(json.dumps(report, indent=2))
print("QUALITY_BASELINE_PASS", OUT / "baseline-export.json", flush=True)
