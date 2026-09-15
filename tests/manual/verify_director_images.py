"""Inspect actual saved native-image materials offline, after the account cache is gone.

Blender --background --factory-startup --python this_file -- material.blend report.json
"""

import hashlib
import json
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.authoring import document, transactions

blend, output = sys.argv[sys.argv.index("--") + 1:]
open_painter.register()
bpy.ops.wm.open_mainfile(filepath=str(Path(blend).resolve()))
texture_sets = list(bpy.context.scene.open_painter.texture_sets)
ts = texture_sets[0]
images = []
for image in bpy.data.images:
    identifier = image.get("director_image_id")
    if not identifier:
        continue
    assert image.packed_file, image.name
    assert hashlib.sha256(bytes(image.packed_file.data)).hexdigest() == identifier.split(":")[1], image.name
    w, h = image.size
    pixels = np.empty(w * h * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    rgb = pixels.reshape(h, w, 4)[..., :3]
    assert np.isfinite(rgb).all() and rgb.std() > .001, image.name
    interior = float((np.abs(np.diff(rgb, axis=0)).mean() + np.abs(np.diff(rgb, axis=1)).mean()) / 2)
    images.append(dict(name=image.name, image_id=identifier, size=[w, h],
        color_space=image.colorspace_settings.name, packed_hash_matches=True,
        pixels_available_offline=True, standard_deviation=float(rgb.std()),
        edge_mean_absolute_difference=dict(horizontal=float(np.abs(rgb[:, 0] - rgb[:, -1]).mean()),
            vertical=float(np.abs(rgb[0] - rgb[-1]).mean()), typical_neighbor=interior)))
assert images, "Expected real generated texture sources"
assignments = []
for part in texture_sets:
    for layer in part.layers:
        for prop in layer.channels:
            for slot in ("image", "detail_image"):
                image = getattr(prop, slot)
                if image and image.get("director_image_id"):
                    assignments.append(dict(material=part.name, layer=layer.name, channel=prop.channel, target=slot,
                        image_id=image["director_image_id"], enabled=prop.enabled))
        for mask in layer.masks:
            if mask.image and mask.image.get("director_image_id"):
                assignments.append(dict(material=part.name, layer=layer.name, mask=mask.name, image_id=mask.image["director_image_id"]))
last = next(item for item in reversed(transactions.history(ts)) if not item.get("reverted"))
group = [part for part in texture_sets if part.id in last.get("scene_targets", [ts.id])]
expected = {part.id: document.canonical(document.decode(next(r for r in reversed(transactions.history(part))
    if not r.get("reverted"))["before"])) for part in group}
transactions.undo(ts)
restored = {part.id: document.canonical(document.snapshot(part)) for part in group} == expected
assert restored, "AI Undo after reopening did not restore the previous material"
report = dict(status="PASS", file=Path(blend).name, generated_images=images,
              assignments=assignments, undo_after_reopen_restored=restored, undo_material_count=len(group),
              note="Offline pixels and hashes checked from packed .blend images; edge differences are diagnostics, not a seamlessness guarantee.")
Path(output).write_text(json.dumps(report, indent=2))
print("DIRECTOR_IMAGES_OFFLINE_PASS", flush=True)
