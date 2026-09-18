"""Build the engine-validation fixtures with the candidate add-on (background Blender).

Exports every fixture of ``tests/integration/quality_fixtures.py`` for the Unreal
(metallic/roughness, DirectX normal, ORM) and Unity URP (Lit metallic, smoothness in the
metallic alpha) targets through the ordinary export plan, plus the FBX mesh of each, and
writes ``fixtures.json`` with the package checksum, the Blender build, every file's sha256
and the expected encoded values of the channel plate for the unlit reference scene.

    blender --background --factory-startup --python engine_validation/build_fixtures.py -- <output folder>
"""
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.export import plan as planning
from open_painter.ui import export_view

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(argv[0] if argv else ROOT / ".blender-test/quality/engine-fixtures")
OUT.mkdir(parents=True, exist_ok=True)
open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
project = scene.open_painter


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def package_checksum():
    candidates = sorted((ROOT / "dist").glob("open_painter-*.zip"), key=lambda p: p.stat().st_mtime) if (ROOT / "dist").exists() else []
    return {"package": candidates[-1].name, "sha256": sha(candidates[-1])} if candidates else {"package": None, "sha256": None,
                                                                                               "note": "no dist ZIP built"}


def commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return None


# Fixtures: (name, builder, note)
built = {}
plate, expected = fixtures.channel_plate(scene, resolution=1024)
plate.name = "Channel plate"
built["channel_plate"] = (plate.id, "unlit channel reference; expected patch values below")
seam, _ = fixtures.seam_object(scene, resolution=2048)
seam.height_distance = 0.0
built["seam_object"] = (seam.id, "lit seam object, no height in the normal")
seam_height, _ = fixtures.seam_object(scene, resolution=2048)
seam_height.name = "Seams height"
seam_height.height_distance = .05
for obj in bpy.data.objects:
    if obj.name == "Seam object.001":
        obj.location.x += 6
built["seam_object_height"] = (seam_height.id, "lit seam object, height included in the normal")
atlas, quads = fixtures.difficult_atlas(scene, resolution=1024)
for obj in bpy.data.objects:
    if obj.name.startswith("Atlas"):
        obj.location.x += 12
built["difficult_atlas"] = (atlas.id, "distance and grazing sweep; the close pair is the diagnostic")
card, opacity = fixtures.transparency_card(scene, resolution=1024)
for obj in bpy.data.objects:
    if obj.name == "Transparency card":
        obj.location.x += 18
built["transparency_card"] = (card.id, "cutout and soft alpha")
body, barrel, plate_set = fixtures.production_prop(scene, resolution=2048, layers=25)
for obj in bpy.data.objects:
    if obj.name.startswith("Prop"):
        obj.location.x += 24
built["production_prop"] = ([body.id, barrel.id, plate_set.id], "production prop (three sets: Body, Barrel, Plate)")

record = {"commit": commit(), "blender": bpy.app.version_string, "os": platform.platform(), "machine": platform.machine(),
          **package_checksum(), "targets": {}, "fixtures": {name: note for name, (_, note) in built.items()},
          "channel_plate_expected": {channel: {"mean": [float(v) for v in values.reshape(-1, 3).mean(axis=0)]}
                                     for channel, values in expected.items()}}

for target_key, folder_name in (("UNREAL", "unreal"), ("UNITY_URP", "unity_urp")):
    project.target = target_key
    project.export_mesh = True
    folder = OUT / folder_name
    files = {}
    for name, (identifiers, note) in built.items():
        identifiers = identifiers if isinstance(identifiers, list) else [identifiers]
        sub = folder / name
        project.export_directory = str(sub)
        chosen = [t for t in project.texture_sets if t.id in identifiers]   # by id: RNA references move
        assert chosen, name
        # Scope: the sets of the fixture's objects (a selection override works in background).
        objects = [ref.object for t in chosen for ref in t.assignments if ref.object]
        with bpy.context.temp_override(selected_objects=objects):
            plan = planning.resolve(bpy.context, contents="TARGET", scope="SELECTED", folder_source="PROJECT")
            written = planning.execute(bpy.context, plan, overwrite=True)
        files[name] = {p.name: sha(p) for p in written}
        for t in chosen:
            files[name][f"{t.name}:padding"] = t.export.last_report
    record["targets"][target_key] = {"folder": str(folder), "files": files}

(OUT / "fixtures.json").write_text(json.dumps(record, indent=2))
print("ENGINE_FIXTURES_BUILT", OUT / "fixtures.json", flush=True)
