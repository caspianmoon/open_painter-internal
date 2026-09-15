"""Fresh material/bake test copy of supplied geometry; never save over the source.

Blender --background --factory-startup --python this_file -- source.blend output_folder
"""

import hashlib
import argparse
import json
from pathlib import Path
import sys
import time

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.core import texture_set_manager as sets
from open_painter.baking import bake_manager
from open_painter.geometry.provenance import status
from open_painter.evaluation import shader_preview

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("directory", type=Path)
parser.add_argument("--full-scene", action="store_true", help="Bake every one of the supplied droid model's ten parts")
args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
source, directory = args.source, args.directory
stem = "fresh-full-scene" if args.full_scene else "fresh-model"
if source.resolve() == (directory / (stem + ".blend")).resolve():
    raise ValueError("Choose a separate output folder; never overwrite the source model")
directory.mkdir(parents=True, exist_ok=True)
original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(source.resolve()))
scene = bpy.context.scene
old = dict(layers=[l.id for ts in scene.open_painter.texture_sets for l in ts.layers],
           maps=[m.image.name for ts in scene.open_painter.texture_sets for m in ts.baked_maps if m.image])
shader_preview.clear()
while scene.open_painter.texture_sets:
    sets.remove(scene, len(scene.open_painter.texture_sets) - 1)
meshes = sorted((o for o in scene.objects if o.type == "MESH"), key=lambda o: o.name)
for obj in meshes:
    obj.data.materials.clear()
for material in list(bpy.data.materials):
    bpy.data.materials.remove(material)
# These are unused old material resources in the test copy, not source geometry.
for image in list(bpy.data.images):
    if image.users <= int(image.use_fake_user):
        bpy.data.images.remove(image)
names = {"defaultMaterial.003": "Director Fresh Body", "defaultMaterial": "Director Fresh Head"}
panels = [o for o in meshes if o.name.rsplit(".", 1)[-1] in {"004", "005", "006", "007", "008", "009"}]
front = min(panels, key=lambda o: sum((o.matrix_world @ Vector(c)).y for c in o.bound_box))
names[front.name] = "Director Fresh Front Panel"
report = dict(source=source.name, source_sha256=original_hash, previous_work_reused=False,
              previous_layers_removed=len(old["layers"]), previous_maps_discarded=len(old["maps"]),
              texture_sets=[])
for obj in meshes:
    ts = sets.create(scene, names.get(obj.name, "Director Fresh " + obj.name), 1024)
    sets.assign(scene, ts, obj)
    report["texture_sets"].append(dict(name=ts.name, object=obj.name, layers_before_ai=0, maps=[]))
for ts in scene.open_painter.texture_sets:
    item = next(item for item in report["texture_sets"] if item["name"] == ts.name)
    if not args.full_scene and ts.name not in names.values():
        continue
    scene.open_painter.active_texture_set = next(i for i, t in enumerate(scene.open_painter.texture_sets) if t.id == ts.id)
    bpy.ops.object.select_all(action="DESELECT")
    obj = ts.assignments[0].object
    obj.select_set(True); bpy.context.view_layer.objects.active = obj
    ts.bake.selected_to_active = False
    ts.bake.samples = 32
    ts.bake.ao_distance = .08
    ts.bake.normal = ts.bake.ao = ts.bake.curvature = True
    ts.bake.position = ts.bake.world_normal = ts.bake.thickness = True
    started = time.monotonic()
    for kind in bake_manager.bake_steps(bpy.context, ts):
        print("FRESH_BAKE", ts.name, kind, flush=True)
    item["bake_seconds"] = round(time.monotonic() - started, 2)
    item["maps"] = [dict(kind=m.kind, image=m.image.name, status=status(ts, m), size=list(m.image.size)) for m in ts.baked_maps]
    assert ts.layers.__len__() == 0
    assert all(m.image.name not in old["maps"] for m in ts.baked_maps)
    assert all(m["status"] == "Ready" for m in item["maps"]), item
    (directory / (stem + ".json")).write_text(json.dumps(report, indent=2))
scene.open_painter.active_texture_set = next(i for i,t in enumerate(scene.open_painter.texture_sets) if t.name == "Director Fresh Body")
bpy.ops.object.select_all(action="DESELECT")
body = bpy.data.objects["defaultMaterial.003"]
body.select_set(True); bpy.context.view_layer.objects.active = body
bpy.ops.wm.save_as_mainfile(filepath=str((directory / (stem + ".blend")).resolve()))
assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
report["original_unchanged"] = True
(directory / (stem + ".json")).write_text(json.dumps(report, indent=2))
print("FRESH_USER_MODEL_PASS", flush=True)
