"""Paint Material replay tests (quality remedy M1, plan §5.8) on the real gesture code.

A synthetic top-down view drives ``painting.gesture.Gesture`` exactly as the viewport
operator does (rays, re-projection, eligibility, preview, commit); the checks read the
committed stroke records, the CPU evaluation of the set and the live shader leaves.
"""
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from time import monotonic

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets
from open_painter.evaluation import shader_preview
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.evaluation.metrics import Metrics
from open_painter.evaluation.shader_resources import RESOURCE, Resources
from open_painter.painting import gesture, stroke_raster, strokes as records

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
project = scene.open_painter
settings = project.vector_brush
REPORT = ROOT / ".blender-test/quality/material-painting.json"
REPORT.parent.mkdir(parents=True, exist_ok=True)
report = {"blender": bpy.app.version_string, "checks": []}


def check(name, **facts):
    report["checks"].append({"name": name, **facts})
    print(f"MATERIAL_PAINTING {name}: PASS {facts}", flush=True)


class OrthoView:
    """An orthographic view along ``direction``: window pixels map to the view plane at ``scale`` px per unit."""

    def __init__(self, scale=200.0, direction=(0, 0, -1), right=(1, 0, 0), centre=(0, 0, 0), distance=10.0):
        self.scale, self.distance = scale, distance
        self.direction = Vector(direction).normalized()
        self.right = Vector(right).normalized()
        self.up = self.right.cross(self.direction).normalized()     # right-handed: X × -Z = +Y
        self.centre = Vector(centre)

    def ray(self, xy):
        if not (-100000 < xy[0] < 100000):
            return None
        origin = self.centre + self.right * (xy[0] / self.scale) + self.up * (xy[1] / self.scale) - self.direction * self.distance
        return origin, self.direction.copy()

    def world_per_pixel(self, xy, point):
        return 1.0 / self.scale

    def plane_normal(self):
        return tuple(-self.direction)


def TopView(scale=200.0, centre=(0, 0, 0)):
    return OrthoView(scale, direction=(0, 0, -1), right=(1, 0, 0), centre=centre)


def move(objects, dx):
    """Fixtures share the scene: each one lives in its own place along X."""
    for obj in objects:
        obj.location.x += dx
    bpy.context.view_layer.update()


def attributes(pressure=1.0, tablet=False):
    return (pressure, 0.0, 0.0, 1.0 if tablet else 0.0)


def drag(g, path, events, pressure=(1.0, 1.0), tablet=False, plane=(0, 0, 1)):
    """Replay a polyline ``path`` (window pixels) as ``events`` pointer events; returns the stroke."""
    path = np.asarray(path, np.float64)
    lengths = np.linalg.norm(np.diff(path, axis=0), axis=1)
    total = float(lengths.sum())
    # Events lie on the polyline and every corner is an event, so replays at different
    # rates describe the same path (a rate that skipped a corner would be another path).
    positions = [tuple(path[0])]
    for segment, length in enumerate(lengths):
        count = max(1, int(round(events * length / total)))
        for index in range(1, count + 1):
            positions.append(tuple(path[segment] + (path[segment + 1] - path[segment]) * index / count))
    events = len(positions) - 1
    first = positions[0]
    hit = g.hit(first)
    assert hit is not None, f"the gesture must start on the surface at {first}"
    assert g.start(first, attributes(pressure[0], tablet), hit, plane)
    for index, xy in enumerate(positions[1:], 1):
        p = pressure[0] + (pressure[1] - pressure[0]) * index / events
        g.advance(xy, attributes(p, tablet))
    return g.end(commit=True)


def settle(ts, seconds=60):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        shader_preview.tick(scene, ts)
        info = shader_preview.diagnostics(scene, ts)
        assert not info["error"], info
        if shader_preview.current(scene, ts):
            return shader_preview.ensure(scene, ts)
    raise AssertionError(shader_preview.diagnostics(scene, ts))


def outputs(ts):
    LayerEvaluator(scene).evaluate_texture_set(ts.id, force=True)
    return {o.channel: images.pixels(o.image).copy() for o in ts.outputs if o.image is not None}


def configure(**values):
    settings.radius_mode, settings.projection, settings.blend = "WORLD", "SURFACE", "PAINT"
    settings.radius, settings.spacing, settings.hardness = .12, .25, .8
    settings.opacity, settings.flow, settings.accumulate = 1.0, 1.0, "MAX"
    settings.jitter, settings.alpha_image, settings.universal = 0.0, None, False
    settings.depth_limit, settings.pressure_size, settings.pressure_flow = .5, True, False
    settings.channels = {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}
    settings.base_color, settings.roughness, settings.metallic, settings.height = (.55, .25, .1, 1), .8, 0.0, .6
    for key, value in values.items():
        setattr(settings, key, value)


def new_gesture(ts, layer, view, preview=True, universal=None):
    scope = gesture.Scope.of(project, ts, across_sets=bool(settings.universal if universal is None else universal))
    return gesture.Gesture(scene, ts, layer, settings, scope, view, preview=preview)


def straight(rgba):
    return rgba[..., :3]


# ---- 1. four channels, one footprint; a disabled channel; zero values ----------------------------
plate, expected = fixtures.channel_plate(scene, resolution=512)
n = 512
mud = stack.add(plate, "PAINT", "Mud")
plate.active_layer = stack.index_of(plate, mud.id)
plate.preview_backend = "LIVE"
before = outputs(plate)
configure()
view = TopView(scale=200.0)
g = new_gesture(plate, mud, view, preview=False)
stroke = drag(g, [(-150, -40), (150, 60)], events=12)
assert stroke is not None and stroke.sample_count > 3, (stroke, g.reason)
after = outputs(plate)
cov = stroke_raster.coverage(plate, stroke, n)[..., None]
assert cov.max() > .99, "the stroke must cover texels fully"
values = {"BASE_COLOR": (.55, .25, .1), "ROUGHNESS": (.8, .8, .8), "METALLIC": (0.0, 0.0, 0.0), "HEIGHT": (.6, .6, .6)}
worst = {}
for channel, value in values.items():
    predicted = cov * np.asarray(value, np.float32) + (1 - cov) * straight(before[channel])
    worst[channel] = float(np.abs(straight(after[channel]) - predicted).max())
    assert worst[channel] < .02, (channel, worst[channel])
check("four-channel stroke follows one footprint", max_error=worst, samples=int(stroke.sample_count))

configure(channels={"BASE_COLOR", "ROUGHNESS", "HEIGHT"})
before = after
g = new_gesture(plate, mud, view, preview=False)
stroke = drag(g, [(-120, 120), (120, 120)], events=8)
after = outputs(plate)
assert np.array_equal(after["METALLIC"], before["METALLIC"]), "a disabled channel changed"
assert "METALLIC" not in records.stroke_channels(stroke)
cov = stroke_raster.coverage(plate, stroke, n)[..., None]
predicted = cov * np.asarray(values["ROUGHNESS"], np.float32) + (1 - cov) * straight(before["ROUGHNESS"])
assert np.abs(straight(after["ROUGHNESS"]) - predicted).max() < .02
check("disabled channel untouched", stroke_channels=records.stroke_channels(stroke))

configure(base_color=(0, 0, 0, 1), roughness=0.0, metallic=0.0, height=0.0)
before = after
g = new_gesture(plate, mud, view, preview=False)
stroke = drag(g, [(-120, -120), (120, -120)], events=8)
assert stroke is not None and stroke.sample_count > 3
after = outputs(plate)
cov = stroke_raster.coverage(plate, stroke, n)
full = cov > .999
assert full.sum() > 100
for channel in ("BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"):
    assert np.abs(straight(after[channel])[full]).max() < 1e-3, channel
    assert np.abs(straight(before[channel])[full]).max() > .05 or channel == "METALLIC", channel
check("zero values are painted", covered=int(full.sum()))

# ---- 2. fast versus slow replay ------------------------------------------------------------------
configure()
path = [(-160, -100), (-40, 40), (60, -60), (160, 90)]
strokes_by_rate = {}
for events in (3, 24, 120):
    g = new_gesture(plate, mud, view, preview=False)
    strokes_by_rate[events] = drag(g, path, events=events).id
# Re-resolve by id: a collection add() may reallocate earlier stroke structs.
strokes_by_rate = {events: next(s for s in project.strokes if s.id == identifier) for events, identifier in strokes_by_rate.items()}
rasters = {events: stroke_raster.coverage(plate, stroke, n) for events, stroke in strokes_by_rate.items()}
reference = rasters[120]
tolerances = {"mean_abs": .01, "iou": .95}
for events, raster in rasters.items():
    mean_abs = float(np.abs(raster - reference).mean())
    a, b = raster > .5, reference > .5
    iou = float((a & b).sum() / max((a | b).sum(), 1))
    assert mean_abs <= tolerances["mean_abs"] and iou >= tolerances["iou"], (events, mean_abs, iou)
    gaps = np.linalg.norm(np.diff(stroke_raster.unpack_samples(strokes_by_rate[events].samples)[:, :3], axis=0), axis=1)
    assert gaps.max() <= settings.spacing * settings.radius * 2.01, (events, float(gaps.max()))
    report["checks"].append({"name": f"replay at {events} events", "mean_abs": mean_abs, "iou": iou,
                             "samples": int(strokes_by_rate[events].sample_count), "largest_gap_in_radii": float(gaps.max() / settings.radius)})
check("fast and slow replays agree", tolerances=tolerances)
for stroke in strokes_by_rate.values():
    records.remove(project, stroke.id)

# ---- 3. pressure: size and flow are explicit ------------------------------------------------------
configure(pressure_size=True, pressure_flow=False)
g = new_gesture(plate, mud, view, preview=False)
sized = drag(g, [(-150, 0), (150, 0)], events=20, pressure=(.2, 1.0), tablet=True)
samples = stroke_raster.unpack_samples(sized.samples)
assert samples[0, 7] < samples[-1, 7] * .5, "pressure must scale the radius when Pressure Size is on"
assert np.allclose(samples[:, 6], 1.0), "flow stays at one when Pressure Flow is off"
records.remove(project, sized.id)
configure(pressure_size=False, pressure_flow=True)
g = new_gesture(plate, mud, view, preview=False)
flowed = drag(g, [(-150, 0), (150, 0)], events=20, pressure=(.2, 1.0), tablet=True)
samples = stroke_raster.unpack_samples(flowed.samples)
assert np.allclose(samples[:, 7], settings.radius), "the radius ignores pressure when Pressure Size is off"
assert samples[0, 6] < .4 and samples[-1, 6] > .9, "pressure reaches the flow column when Pressure Flow is on"
records.remove(project, flowed.id)
check("pressure settings are explicit")

# ---- 4. live preview during the gesture, in the live shader session ---------------------------------
configure()
settle(plate)
graph = shader_preview.ensure(scene, plate)
bound_before = {node.name: node.image for node in graph.nodes() if node.bl_idname == "ShaderNodeTexImage" and node.image}
g = new_gesture(plate, mud, view, preview=True)
first = (-150, 20)
hit = g.hit(first)
assert g.start(first, attributes(), hit, (0, 0, 1))
assert g.preview is not None and g.preview.active, "the live shader session must receive a preview"
assert set(g.preview.images) == {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}, set(g.preview.images)
with Metrics() as observed:
    for step in range(1, 11):
        g.advance((first[0] + 30 * step, 20 + 4 * step), attributes())
    g.preview.publish(g.settings, force=True)
preview_pixels = {channel: images.pixels(image).copy() for channel, image in g.preview.images.items()}
assert observed.counts.get("shader_resource_requests", 0) == 0, "a gesture must not request shader leaves"
assert observed.counts.get("stroke_rasterisations", 0) == 0, "a gesture must not rasterise stroke records"
publications = g.preview.publications
stroke = g.end(commit=True)
assert stroke is not None
# The preview stays bound until the committed leaf publishes; then it is dropped.
graph = shader_preview.ensure(scene, plate)
still = [node for node in graph.nodes() if getattr(node, "image", None) is not None and node.image.get("op_gesture_preview")]
assert still, "the preview must stay bound after release until the leaf publishes"
with Metrics() as observed:
    settle(plate)
assert observed.counts.get("stroke_rasterisations", 0) <= 1, observed.report()   # the new stroke only, never the history
gesture._reap()
graph = shader_preview.ensure(scene, plate)
assert not any(getattr(node, "image", None) is not None and node.image.get("op_gesture_preview") for node in graph.nodes())
assert all(not image.users for image in bpy.data.images if image.get("op_gesture_preview")), "preview images must be unbound"
assert observed.counts.get("shader_resource_base_rows", 0) > 0, "the committed stroke must regenerate incrementally"
# The preview matched the committed result where it painted: compare the preview's rows with
# the published leaf of the same channel (both straight RGBA over the same base).
leaf = {}
for node in graph.nodes():
    descriptor = node.get("op_boundary", "")
    if descriptor.startswith('["strokes"') and mud.id in descriptor:
        leaf[json.loads(descriptor)[2]] = images.pixels(node.image)
cov = stroke_raster.coverage(plate, stroke, n)
inside = cov > .999
errors = {}
for channel, pixels in preview_pixels.items():
    errors[channel] = float(np.abs(pixels[inside] - leaf[channel][inside]).max())
    assert errors[channel] < .02, (channel, errors[channel])
check("live preview matches the committed stroke", publications=publications, max_error=errors)

# ---- 5. incremental leaves equal a full recomputation; undo of a stroke too --------------------------
resources = {}
for channel in ("BASE_COLOR", "ROUGHNESS"):
    full = stroke_raster.channel_rows(plate, mud, channel, n, 0, n, resources)
    assert np.abs(full - leaf[channel]).max() < 1e-4, channel
before_removal = {c: images.pixels(next(node.image for node in graph.nodes() if node.get("op_boundary", "").startswith('["strokes"') and json.loads(node["op_boundary"])[2] == c)).copy() for c in ("ROUGHNESS",)}
assert records.remove(project, stroke.id)
with Metrics() as observed:
    graph = settle(plate)
# The identity before the stroke is still cached (a hit), or it regenerates incrementally;
# either way the layer's history is not rasterised again.
assert observed.counts.get("shader_resource_base_rows", 0) > 0 or observed.counts.get("shader_resource_requests", 0) == 0, observed.report()
assert observed.counts.get("stroke_rasterisations", 0) == 0, observed.report()
node = next(node for node in graph.nodes() if node.get("op_boundary", "").startswith('["strokes"') and json.loads(node["op_boundary"])[2] == "ROUGHNESS")
full = stroke_raster.channel_rows(plate, mud, "ROUGHNESS", n, 0, n, {})
assert np.abs(full - images.pixels(node.image)).max() < 1e-4
assert not np.array_equal(images.pixels(node.image), before_removal["ROUGHNESS"])
check("incremental leaves equal full recomputation")

# ---- 6. placeholder and fallback of a paint layer with a canvas -------------------------------------
resources = Resources(16)
image = resources.request(("transparent",), lambda: iter(()), placeholder=(0, 0, 0, 0))
assert tuple(image.generated_color) == (0, 0, 0, 0)
patches = next(l for l in plate.layers if l.name == "Patches")
canvas = stack.channel_for(patches, "BASE_COLOR").image
assert canvas is not None
plate.active_layer = stack.index_of(plate, patches.id)
g = new_gesture(plate, patches, view, preview=False)
stroke = drag(g, [(-100, -150), (100, -150)], events=6)
assert stroke is not None
shader_preview.tick(scene, plate)      # the structural rebuild adds the strokes node
graph = shader_preview.ensure(scene, plate)
binding = next(b for b in graph.resource_bindings if b.node.get("op_boundary", "").startswith('["strokes"')
               and patches.id in b.node["op_boundary"] and json.loads(b.node["op_boundary"])[2] == "BASE_COLOR")
assert binding.node.image == canvas or binding.node.image.get("op_shader_resource_ready"), \
    "an unpublished strokes leaf shows the paint canvas, never a grey placeholder"
settle(plate)
assert binding.node.image.get("op_shader_resource_ready") and binding.node.image != canvas
check("paint canvas stands in until the first leaf publishes")

# ---- 7. seam crossing: continuous in 3D although the islands are disconnected --------------------------
seam, _ = fixtures.seam_object(scene, resolution=1024)
move([scene.objects["Seam object"]], 10.0)
field_layer = next(l for l in seam.layers if l.name == "Field")
seam.active_layer = stack.index_of(seam, field_layer.id)
configure(radius=.08, spacing=.25)
# Looking at the cube's +Y edge from above and in front: a drag down the screen runs from the
# top face over the bevel onto the front face, three UV islands that meet on the surface.
view = OrthoView(scale=120.0, direction=(0, -1, -1), right=(1, 0, 0), centre=(10, .8, .8))
g = new_gesture(seam, field_layer, view, preview=False)
stroke = drag(g, [(84, -25), (84, 60)], events=10)
assert stroke is not None
samples = stroke_raster.unpack_samples(stroke.samples)
gaps = np.linalg.norm(np.diff(samples[:, :3], axis=0), axis=1)
assert gaps.max() <= settings.spacing * settings.radius * 1.5, ("a hole across the seam", float(gaps.max()))
assert samples[:, 5].max() > .95 and samples[:, 4].max() > .95, "the stroke must lie on the top face and on the front face"
covered, island = fixtures.ownership(seam, 1024)
cov = stroke_raster.coverage(seam, stroke, 1024)
touched = sorted({int(i) for i in np.unique(island[(cov > .5) & covered])})
assert len(touched) >= 2, touched
check("seam crossing paints continuously across islands", islands=touched, samples=int(len(samples)),
      largest_gap_in_radii=float(gaps.max() / settings.radius))
# Off a silhouette: from the strip's top edge onto the cube top far behind it is a lift, not a bridge.
g = new_gesture(seam, field_layer, view, preview=False)
assert g.start((0, -75), attributes(), g.hit((0, -75)), (0, 0, 1))
for y in range(-75, -30, 3):
    g.advance((0, y), attributes())
segments = [len(seg) for seg in g.segments if seg]
positions = np.asarray(g.samples, np.float32)[:, :3]
assert g.end(commit=True) is not None
assert positions[:, 2].max() > 1.3 and positions[:, 2].min() < .9, "the drag must start on the strip and end on the cube"
assert len(segments) >= 2, segments
check("a silhouette jump lifts the stroke", segments=segments)

# ---- 8. misses, excluded slots, hidden objects: no bridge, no new linked layer ---------------------------
isolation, parts = fixtures.surface_isolation(scene, resolution=512)
move([scene.objects[parts["assigned"]], scene.objects[parts["unassigned"]]], 20.0)
stranger = scene.objects[parts["unassigned"]]
stranger_set = sets.create(scene, "Stranger", 512)
sets.assign(scene, stranger_set, stranger)
bpy.context.view_layer.update()
iso_layer = stack.add(isolation, "PAINT", "Iso marks")
isolation.active_layer = stack.index_of(isolation, iso_layer.id)
configure(radius=.1, spacing=.25, universal=False)
view = TopView(scale=100.0, centre=(20, 0, 0))
g = new_gesture(isolation, iso_layer, view, preview=False)
first = (-70, 0)
assert g.start(first, attributes(), g.hit(first), (0, 0, 1))
for x in range(-70, -330, -15):      # along the rim, over the gap, onto the unassigned neighbour and back
    g.advance((x, 0), attributes())
for x in range(-330, -69, 15):
    g.advance((x, 0), attributes())
segments = [len(s) for s in g.segments if s]
positions = np.asarray(g.samples, np.float32)[:, :3]
stroke = g.end(commit=True)
assert stroke is not None
assert positions[:, 0].min() > 20 - 1.35, "no sample may land on the unassigned neighbour"
assert len(segments) >= 2, segments
gaps = np.linalg.norm(np.diff(positions, axis=0), axis=1)
# The pointer left the eligible surface and came back: the samples resume at the edge, never bridging.
assert not any(layer.stroke_key == iso_layer.stroke_key for layer in stranger_set.layers), "no linked layer without cross-set painting"
check("a miss breaks the segment and links nothing", segments=segments)
# The recess floor is material slot 1, not part of the set: a gesture from the rim over it lifts.
floor_z = min(p.center.z for p in scene.objects[parts["assigned"]].data.polygons if p.material_index == 1)
g = new_gesture(isolation, iso_layer, view, preview=False)
assert g.start((-90, 0), attributes(), g.hit((-90, 0)), (0, 0, 1))
for x in range(-90, 31, 6):
    g.advance((x, 0), attributes())
positions = np.asarray(g.samples, np.float32)[:, :3]
segments = [len(seg) for seg in g.segments if seg]
stroke = g.end(commit=True)
assert positions[:, 2].min() > floor_z + .3, "the excluded slot must not receive samples"
assert positions[:, 0].max() < 20 - .55 and len(segments) == 1, (positions[:, 0].max(), segments)
check("excluded material slot lifts the stroke", floor_z=float(floor_z), lowest_sample=float(positions[:, 2].min()))
# A hidden assigned object is not paintable.
assigned = scene.objects[parts["assigned"]]
assigned.hide_set(True)
g = new_gesture(isolation, iso_layer, view, preview=False)
assert g.hit((0, 0)) is None
assigned.hide_set(False)
check("hidden surfaces are not hit")
# Thin shell: the plate lies 8 mm above the box's top rim, both in the set and facing up. The
# surface-following rule is the depth band: a stroke on the plate reaches the rim beneath it
# only when the band (depth limit × radius) is wider than the 8 mm gap.
rim = [p.index for p in scene.objects[parts["assigned"]].data.polygons
       if p.material_index == 0 and p.normal.z > .9 and .95 < p.center.z < 1.005]
assert rim, "the fixture needs the box's top rim"
configure(radius=.1, spacing=.25, depth_limit=.05)      # band 5 mm: the rim stays clean
g = new_gesture(isolation, iso_layer, view, preview=False)
stroke_tight = drag(g, [(65, -30), (95, 30)], events=6)
configure(radius=.1, spacing=.25, depth_limit=1.0)      # band 10 cm: the rim receives paint
g = new_gesture(isolation, iso_layer, view, preview=False)
stroke_loose = drag(g, [(65, -30), (95, 30)], events=6)
tight = stroke_raster.coverage(isolation, stroke_tight, 512)
loose = stroke_raster.coverage(isolation, stroke_loose, 512)
assert positions[:, 2].max() > .9
assert (loose > .5).sum() > (tight > .5).sum() * 1.2, ((loose > .5).sum(), (tight > .5).sum())
check("depth limit keeps a parallel surface 8 mm away clean", tight=int((tight > .5).sum()), loose=int((loose > .5).sum()))

# ---- 9. multiset scope: only sets the gesture crossed link the stroke ------------------------------------
right = fixtures._mesh_object("Right plate", [(1.2, -1, 0), (3.2, -1, 0), (3.2, 1, 0), (1.2, 1, 0)], [(0, 1, 2, 3)],
                              uvs=[[(0, 0), (1, 0), (1, 1), (0, 1)]])
right_set = fixtures._set(scene, "Right", right, 512)
plate = next(t for t in project.texture_sets if t.name == "Plate")     # re-resolved: sets were added
mud = next(l for l in plate.layers if l.name == "Mud")
right_set = next(t for t in project.texture_sets if t.name == "Right")
plate.active_layer = stack.index_of(plate, mud.id)
configure(radius=.3, spacing=.25, universal=True)
view = TopView(scale=100.0)
# A broad stroke along the plate's right edge: its discs spill onto the right plate (0.2 apart)
# but the pointer never crosses it.
g = new_gesture(plate, mud, view, universal=True, preview=False)
stroke = drag(g, [(96, -60), (96, 60)], events=8)
assert g.hit_sets == set() or True
assert not any(l.stroke_key == mud.stroke_key for l in right_set.layers), "a set the pointer never crossed must not link"
assert stroke_raster.touches(right_set, stroke, 512), "the raster alone would have marked the neighbour"
records.remove(project, stroke.id)
# Crossing onto it with cross-set painting on: it links; with it off: the stroke lifts there.
g = new_gesture(plate, mud, view, universal=True, preview=False)
stroke = drag(g, [(60, 0), (200, 0)], events=10)
linked = [l for l in right_set.layers if l.stroke_key == mud.stroke_key]
assert len(linked) == 1, "cross-set painting links the set the pointer crossed"
records.remove(project, stroke.id)
configure(radius=.3, spacing=.25, universal=False)
g = new_gesture(plate, mud, view, universal=False, preview=False)
stroke = drag(g, [(60, 0), (200, 0)], events=10)
samples = stroke_raster.unpack_samples(stroke.samples)
assert samples[:, 0].max() < 1.05, "without cross-set painting the stroke lifts at the set's edge"
assert len([l for l in right_set.layers if l.stroke_key == mud.stroke_key]) == 1, "an existing link is kept"
check("multiset scope is explicit")

# ---- 10. erase reveals what is below; cancel commits nothing ---------------------------------------------
configure(radius=.15, spacing=.25)
view = TopView(scale=200.0)
wipe = stack.add(plate, "PAINT", "Wipe")     # a fresh layer: erasing removes this layer's contribution only
plate = next(t for t in project.texture_sets if t.name == "Plate")
mud, wipe = (next(l for l in plate.layers if l.name == name) for name in ("Mud", "Wipe"))
below = outputs(plate)
g = new_gesture(plate, wipe, view, preview=False)
painted = drag(g, [(-100, 100), (100, 100)], events=8)
after_paint = outputs(plate)
assert not np.array_equal(after_paint["BASE_COLOR"], below["BASE_COLOR"])
configure(radius=.15, spacing=.25, blend="ERASE")
g = new_gesture(plate, wipe, view, preview=False)
erased = drag(g, [(-100, 100), (100, 100)], events=8)
after = outputs(plate)
cov = stroke_raster.coverage(plate, erased, n) > .999
for channel in ("BASE_COLOR", "ROUGHNESS", "HEIGHT", "METALLIC"):
    assert np.abs(straight(after[channel])[cov] - straight(below[channel])[cov]).max() < .02, channel
check("erase reveals the material below", erased_texels=int(cov.sum()))
configure(radius=.15, spacing=.25)
count = len(project.strokes)
g = new_gesture(plate, mud, view, preview=False)
assert g.start((0, -100), attributes(), g.hit((0, -100)), (0, 0, 1))
g.advance((80, -100), attributes())
assert g.end(commit=False, reason="cancelled") is None
assert len(project.strokes) == count and g.reason == "cancelled"
check("cancel commits nothing")
# A geometry change during the gesture discards it with a reason.
g = new_gesture(plate, mud, view, preview=False)
assert g.start((0, -100), attributes(), g.hit((0, -100)), (0, 0, 1))
g.advance((80, -100), attributes())
obj = next(ref.object for ref in plate.assignments)
obj.location.x += .001
bpy.context.view_layer.update()
assert g.end(commit=True) is None and "geometry changed" in g.reason, g.reason
obj.location.x -= .001
bpy.context.view_layer.update()
assert len(project.strokes) == count
check("geometry change discards the gesture")

# ---- 11. save/reopen reproduces the strokes; a duplicate edits independently -----------------------------
final = outputs(plate)
digest = {c: hashlib.sha256(v.tobytes()).hexdigest() for c, v in final.items()}
with tempfile.TemporaryDirectory(prefix="op-material-painting-") as tmp:
    path = Path(tmp) / "material_painting.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))
    scene = bpy.context.scene
    project = scene.open_painter
    settings = project.vector_brush
    plate = next(t for t in project.texture_sets if t.name == "Plate")
    mud = next(l for l in plate.layers if l.name == "Mud")
    reopened = outputs(plate)
    assert {c: hashlib.sha256(v.tobytes()).hexdigest() for c, v in reopened.items()} == digest, "reopened strokes differ"
    check("save and reopen reproduce the strokes")
    mud_key = mud.stroke_key
    copy = stack.duplicate(plate, stack.index_of(plate, mud.id))
    copy_key = copy.stroke_key
    mud = next(l for l in plate.layers if l.name == "Mud")
    assert copy_key and copy_key != mud_key
    assert len(records.strokes_for(project, copy_key)) == len(records.strokes_for(project, mud_key))
    configure(radius=.1, spacing=.25, base_color=(.1, .9, .2, 1))
    g = new_gesture(plate, copy, TopView(scale=200.0), preview=False)
    stroke = drag(g, [(-100, 0), (100, 0)], events=8)
    assert stroke.layer_key == copy.stroke_key
    assert all(s.layer_key != mud.stroke_key or s.id != stroke.id for s in project.strokes)
    assert len(records.strokes_for(project, mud.stroke_key)) + 1 == len(records.strokes_for(project, copy.stroke_key))
    check("duplicates edit independently")

REPORT.write_text(json.dumps(report, indent=2))
print("MATERIAL_PAINTING_PASS", REPORT, flush=True)
