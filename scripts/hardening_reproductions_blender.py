"""Reproduce the two painting findings of the 0.13.0 audit in background Blender (hardening plan H0),
with the same script on the audited checkout and on the hardened one:

    blender --background --factory-startup --python scripts/hardening_reproductions_blender.py -- --out <json>

[C2] a stroke recorded without cross-set painting still changes a set an earlier cross-set stroke
     linked (compared on the excluded set's actual BASE_COLOR pixels);
[C3] a stroke on a plate 8 mm above a same-facing rim paints the rim inside the depth band with the
     brush's default settings (compared on the rim's texels).

The interactive finding [C1] (the target changed while the tool stays alive) needs the modal
operator: tests/integration/material_target_ui.py with OP_MATERIAL_TARGET_REPRODUCE=1 records it.
Only APIs both checkouts share are used; nothing is asserted, verdicts are recorded.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter  # noqa: E402
import quality_fixtures as fixtures  # noqa: E402
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets  # noqa: E402
from open_painter.evaluation.evaluator import LayerEvaluator  # noqa: E402
from open_painter.painting import gesture, stroke_raster  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else ROOT / ".blender-test/quality/hardening/painting-reproductions.json"

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
project = scene.open_painter
settings = project.vector_brush
head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
record = {"revision": head, "blender": bpy.app.version_string, "brush_has_reach": hasattr(settings, "reach"), "cases": {}}


class TopView:
    def __init__(self, scale=100.0, centre=(0, 0, 0)):
        self.scale, self.centre = scale, np.asarray(centre, np.float64)

    def ray(self, xy):
        from mathutils import Vector
        origin = Vector((self.centre[0] + xy[0] / self.scale, self.centre[1] + xy[1] / self.scale, self.centre[2] + 10.0))
        return origin, Vector((0, 0, -1))

    def world_per_pixel(self, xy, point):
        return 1.0 / self.scale

    def plane_normal(self):
        return (0.0, 0.0, 1.0)


def configure(**values):
    settings.radius_mode, settings.projection, settings.blend = "WORLD", "SURFACE", "PAINT"
    settings.radius, settings.spacing, settings.hardness = .12, .25, .8
    settings.opacity, settings.flow, settings.accumulate = 1.0, 1.0, "MAX"
    settings.jitter, settings.alpha_image, settings.universal = 0.0, None, False
    settings.depth_limit, settings.pressure_size, settings.pressure_flow = .5, True, False
    settings.channels = {"BASE_COLOR"}
    settings.base_color = (.55, .25, .1, 1)
    if hasattr(settings, "reach"):
        settings.property_unset("reach")   # the brush's own default (Surface on the hardened checkout)
    for key, value in values.items():
        setattr(settings, key, value)


def drag(ts, layer, view, path, universal):
    scope = gesture.Scope.of(project, ts, across_sets=universal)
    g = gesture.Gesture(scene, ts, layer, settings, scope, view, preview=False)
    first = tuple(path[0])
    hit = g.hit(first)
    assert hit is not None, first
    assert g.start(first, (1.0, 0.0, 0.0, 0.0), hit, (0, 0, 1))
    for xy in path[1:]:
        g.advance(tuple(xy), (1.0, 0.0, 0.0, 0.0))
    return g, g.end(commit=True)


def color_digest(ts):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    image = next(o.image for o in ts.outputs if o.channel == "BASE_COLOR")
    return hashlib.sha256(images.pixels(image).tobytes()).hexdigest()


def texels_of(ts, polygons, n, object_name):
    from open_painter.geometry.snapshots import targets
    from open_painter.geometry.uv_rasterizer import rasterize
    mask = np.zeros((n, n), bool)
    with targets(ts) as entries:
        for target in entries:
            if target["object"].name != object_name:
                continue
            chosen = np.isin(target["tri_polygons"], np.asarray(list(polygons), np.int64))
            if chosen.any():
                rows, cols, _, _ = rasterize(target["uv"][target["tri_loops"][chosen]], n)
                mask[rows, cols] = True
    return mask


# ---- [C2] cross-set link, then a single-set stroke whose disc spills onto the linked set ----------
def cross_set_scope():
    plate = fixtures._mesh_object("Scope plate", [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [(0, 1, 2, 3)],
                                  uvs=[[(0, 0), (1, 0), (1, 1), (0, 1)]])
    right = fixtures._mesh_object("Scope right", [(1.2, -1, 0), (3.2, -1, 0), (3.2, 1, 0), (1.2, 1, 0)], [(0, 1, 2, 3)],
                                  uvs=[[(0, 0), (1, 0), (1, 1), (0, 1)]])
    plate_set = fixtures._set(scene, "ScopePlate", plate, 512)
    right_set = fixtures._set(scene, "ScopeRight", right, 512)
    plate_set = next(t for t in project.texture_sets if t.name == "ScopePlate")
    right_set = next(t for t in project.texture_sets if t.name == "ScopeRight")
    for ts in (plate_set, right_set):
        ts.preview_backend = "CPU"
        base = stack.add(ts, "FILL", "Base")
        stack.channel_for(base, "BASE_COLOR").color = (.5, .5, .5, 1)
    mud = stack.add(plate_set, "PAINT", "Mud")
    plate_set.active_layer = stack.index_of(plate_set, mud.id)
    view = TopView(scale=100.0)
    configure(radius=.3, spacing=.25, universal=True, depth_limit=1.0)
    _, crossing = drag(plate_set, mud, view, [(60, 0), (130, 0), (200, 0)], universal=True)
    linked = [l for l in right_set.layers if l.stroke_key == mud.stroke_key]
    before = color_digest(right_set)
    configure(radius=.3, spacing=.25, universal=False, depth_limit=1.0)
    if hasattr(settings, "reach"):
        settings.reach = "DEPTH"    # the broadest reach of the hardened brush: the disc does spill
    _, single = drag(plate_set, mud, view, [(60, 40), (100, 40), (118, 40)], universal=False)
    spills = bool(stroke_raster.touches(right_set, single, 512))
    after = color_digest(right_set)
    return {"linked_layers_in_right_set": len(linked), "single_stroke_disc_reaches_right_set": spills,
            "right_set_before": before, "right_set_after": after,
            "single_stroke_scope": getattr(single, "scope", "<no scope field>"),
            "verdict": "PASS" if (before == after and spills and len(linked) == 1) else "FAIL: the excluded set's pixels changed"}


# ---- [C3] a plate 8 mm above a same-facing rim ------------------------------------------------------
def rear_sheet():
    isolation, parts = fixtures.surface_isolation(scene, resolution=512)
    for obj in (scene.objects[parts["assigned"]], scene.objects[parts["unassigned"]]):
        obj.location.x += 20.0
    bpy.context.view_layer.update()
    isolation = next(t for t in project.texture_sets if t.name == "Isolation")
    isolation.preview_backend = "CPU"
    layer = stack.add(isolation, "PAINT", "Iso marks")
    isolation.active_layer = stack.index_of(isolation, layer.id)
    rim = [p.index for p in scene.objects[parts["assigned"]].data.polygons
           if p.material_index == 0 and p.normal.z > .9 and .95 < p.center.z < 1.005]
    rim_texels = texels_of(isolation, rim, 512, parts["assigned"])
    view = TopView(scale=100.0, centre=(20, 0, 0))
    configure(radius=.1, spacing=.25, depth_limit=1.0)   # the brush's defaults otherwise
    _, stroke = drag(isolation, layer, view, [(65, -30), (75, -10), (85, 10), (95, 30)], universal=False)
    coverage = stroke_raster.coverage(isolation, stroke, 512)
    hit = int((coverage[rim_texels] > .5).sum())
    return {"rim_texels": int(rim_texels.sum()), "rim_texels_painted": hit, "plate_texels_painted": int((coverage > .5).sum()),
            "stroke_reach": getattr(stroke, "reach", "<no reach field>"),
            "verdict": "PASS" if hit == 0 and (coverage > .5).sum() > 100 else "FAIL: the hidden same-facing rim received paint"}


for name, fn in (("cross_set_scope_after_link", cross_set_scope), ("rear_sheet_in_default_mode", rear_sheet)):
    try:
        record["cases"][name] = fn()
    except Exception as error:   # recorded, never hidden
        import traceback
        record["cases"][name] = {"exception": traceback.format_exc()}
    print(name, json.dumps(record["cases"][name], default=str)[:500], flush=True)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
print("PAINTING_REPRODUCTIONS", out, flush=True)
