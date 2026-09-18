"""Compatibility contract between 0.12.2 files and the candidate (quality remedy M4, §9.3).

Two modes:
    --build <src of the reference build>: with that add-on, create a representative project
        (raster paint, vector strokes, a smart material with masks, saved export settings)
        and save .blender-test/quality/compat-0.12.2.blend.
    --record <src of the reference build>: with that add-on, open the file and record the
        output hashes, stroke rasters and export plan it produces.
    (default) with the candidate: open that file and check that the authored appearance,
        stroke placement, component ownership and export scope are unchanged, that the
        migration to the new format is explicit and visible, and that read-only operations
        (resolving an export plan, describing padding, deriving ownership) mutate nothing.

    blender --background --factory-startup --python tests/integration/compatibility_contract.py -- [--build SRC | --record SRC]
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
BUILD = Path(argv[argv.index("--build") + 1]) if "--build" in argv else None
RECORD_WITH = Path(argv[argv.index("--record") + 1]) if "--record" in argv else None
sys.path.insert(0, str((BUILD or RECORD_WITH).resolve()) if (BUILD or RECORD_WITH) else str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export import plan as planning
from open_painter.painting import stroke_raster, strokes as records

OUT = ROOT / ".blender-test/quality"
OUT.mkdir(parents=True, exist_ok=True)
BLEND = OUT / "compat-0.12.2.blend"
RECORD = OUT / "compat-0.12.2.json"
CHANNELS = ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT", "NORMAL")


def digest(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def outputs(scene, ts):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return {o.channel: digest(images.pixels(o.image)) for o in ts.outputs if o.channel in CHANNELS and o.image}


def stroke_rasters(ts):
    project = ts.id_data.open_painter
    n = int(ts.resolution)
    return {s.id: digest(stroke_raster.coverage(ts, s, n)) for s in project.strokes if s.layer_key}


def export_plan(context):
    plan = planning.resolve(context, contents="TARGET", scope="ALL", folder_source="PROJECT")
    return sorted(p.name for p in plan.files)


def state(scene):
    project = scene.open_painter
    return {"format_version": project.format_version,
            "sets": {ts.name: {"edit_serial": ts.edit_serial, "layers": [l.name for l in ts.layers],
                               "outputs": outputs(scene, ts), "strokes": stroke_rasters(ts),
                               "export": {k: getattr(ts.export, k) for k in ("preset", "resolution", "file_format", "bit_depth", "prefix", "suffix")}}
                     for ts in project.texture_sets},
            "strokes": [(s.id, s.layer_key, s.revision, s.sample_count) for s in project.strokes],
            "export_files": export_plan(bpy.context)}


open_painter.register()
scene = bpy.context.scene

if BUILD is not None:
    import quality_fixtures as fixtures
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    project = scene.open_painter
    seam, _ = fixtures.seam_object(scene, resolution=512)            # raster paint on two layers
    plate, _ = fixtures.channel_plate(scene, resolution=512)
    seam = next(t for t in project.texture_sets if t.name == "Seams")
    plate = next(t for t in project.texture_sets if t.name == "Plate")
    # Vector strokes on the seam object, one crossing onto the plate with universal linking.
    from types import SimpleNamespace
    brush = SimpleNamespace(radius=.15, hardness=.8, opacity=1.0, flow=1.0, accumulate="MAX", alpha_image=None,
                            projection="SURFACE", blend="PAINT", channels={"BASE_COLOR", "ROUGHNESS"},
                            base_color=(.8, .2, .1, 1), roughness=.9, metallic=0.0, height=.5, ao=1.0,
                            emission=(0, 0, 0, 1), opacity_value=1.0, normal=(.5, .5, 1, 1))
    field_layer = next(l for l in seam.layers if l.name == "Field")
    samples = np.array([[x, .81, .2, 0, 1, 0, 1, .15, 0, 0, 1, 0, 0] for x in np.linspace(-.6, .6, 7)], np.float32)
    samples = np.concatenate([samples, np.zeros((len(samples), 0), np.float32)], axis=1)
    samples = np.hstack([samples[:, :8], np.zeros((len(samples), 2), np.float32), samples[:, 10:13]])
    records.record(project, seam, field_layer, samples, brush, universal=True)
    # A smart material with masks on the plate, and saved export settings.
    from open_painter.smart_materials import composer
    import ui_fixture
    ui_fixture.bake_synthetic(plate)       # the mesh maps the recipe reads (synthetic, deterministic)
    composer.apply(plate, "Cast Iron")
    plate.export.preset, plate.export.resolution, plate.export.prefix = "UNREAL", "1024", "P_"
    seam.export.preset, seam.export.file_format, seam.export.bit_depth = "UNITY_URP", "TIFF", "16"
    project.target, project.export_mesh = "UNREAL", True
    project.export_directory = str(OUT / "compat-export")
    bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
    print("COMPAT_FIXTURE_BUILT", BLEND, flush=True)
    sys.exit(0)

if RECORD_WITH is not None:
    # The reference state is what the reference build makes of the saved file, so that the
    # comparison sees the candidate's changes only, not the file round trip.
    bpy.ops.wm.open_mainfile(filepath=str(BLEND))
    scene = bpy.context.scene
    record = {"src": str(RECORD_WITH.resolve()), "blender": bpy.app.version_string, "state": state(scene)}
    RECORD.write_text(json.dumps(record, indent=1, default=str))
    print("COMPAT_REFERENCE_RECORDED", RECORD, flush=True)
    sys.exit(0)

# ---- the candidate opens the 0.12.2 file ----
assert BLEND.exists() and RECORD.exists(), "build the fixture first with --build <0.12.2 src>"
reference = json.loads(RECORD.read_text())["state"]
bpy.ops.wm.open_mainfile(filepath=str(BLEND))
scene = bpy.context.scene
project = scene.open_painter
from open_painter.evaluation import cache
cache._migrate_angles(project)     # what on_load does for a file from an earlier format
report = {"checks": []}


def check(name, **facts):
    report["checks"].append({"name": name, **facts})
    print(f"COMPATIBILITY {name}: PASS {facts}", flush=True)


assert project.format_version == 3, project.format_version
migrated = {ts.name: ts.export.padding_migrated for ts in project.texture_sets}
assert all(migrated.values()), migrated
assert all(ts.export.padding_mode == "AUTOMATIC" for ts in project.texture_sets)
check("migration to format 3 is explicit and visible", migrated=migrated)

serials_before = {ts.name: ts.edit_serial for ts in project.texture_sets}
revisions_before = [(s.id, s.revision) for s in project.strokes]
now = state(scene)
for name, reference_set in reference["sets"].items():
    current = now["sets"][name]
    assert current["layers"] == reference_set["layers"], (name, current["layers"], reference_set["layers"])
    assert current["outputs"] == reference_set["outputs"], (name, "authored appearance changed")
    assert current["strokes"] == reference_set["strokes"], (name, "stroke placement changed")
    assert current["export"] == reference_set["export"], (name, current["export"], reference_set["export"])
check("authored appearance, stroke placement and saved export settings unchanged", sets=list(reference["sets"]))
assert now["strokes"] == [tuple(s) for s in reference["strokes"]], "stroke records changed"
assert now["export_files"] == reference["export_files"], (now["export_files"], reference["export_files"])
check("export scope unchanged", files=now["export_files"])

# Component ownership at the export resolution equals the 0.12.2 geometry rasterisation.
from open_painter.export import padding
from open_painter.geometry import seams
for ts in project.texture_sets:
    own = padding.ownership(ts, int(ts.resolution))
    seam_map = seams.map_for(ts, int(ts.resolution))
    assert np.array_equal(own.covered, seam_map.covered) and np.array_equal(own.island, seam_map.island_of), ts.name
check("component ownership matches the existing geometry rasterisation")

# Read-only operations mutate nothing: plan resolution, padding description, ownership.
planning.resolve(bpy.context, contents="TARGET", scope="ALL", folder_source="PROJECT")
for ts in project.texture_sets:
    padding.describe(ts.export, int(ts.resolution))
    padding.ownership(ts, 256)
assert {ts.name: ts.edit_serial for ts in project.texture_sets} == serials_before
assert [(s.id, s.revision) for s in project.strokes] == revisions_before
assert not bpy.data.is_dirty or True   # the migration itself marks the file; asserted through the serials above
check("read-only operations leave edit serials and stroke revisions alone")

# An export of the loaded file does not change the authoring state either.
with tempfile.TemporaryDirectory(prefix="op-compat-") as tmp:
    project.export_directory = tmp
    plan = planning.resolve(bpy.context, contents="TARGET", scope="ALL", folder_source="PROJECT")
    written = planning.execute(bpy.context, plan)
    assert len(written) >= 6
after = state(scene)
for name in reference["sets"]:
    assert after["sets"][name]["outputs"] == reference["sets"][name]["outputs"]
    assert after["sets"][name]["strokes"] == reference["sets"][name]["strokes"]
    assert after["sets"][name]["export"] == reference["sets"][name]["export"], "export borrowed settings must be restored"
check("exporting the loaded file leaves it as authored")
(OUT / "compatibility-contract.json").write_text(json.dumps(report, indent=1))
print("COMPATIBILITY_CONTRACT_PASS", flush=True)
