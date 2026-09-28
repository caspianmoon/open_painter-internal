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
from open_painter.painting import gesture, stroke_raster, strokes as records, surface_reach

# This suite asserts legacy Blender-image boundaries and incremental CPU rows.
# GPU input/adoption paths are exercised by authoring_workflow and GUI painting.
from unittest.mock import patch
from open_painter.evaluation.engine.compiler import EngineGraph
from open_painter.evaluation.shader_graph import Graph
from open_painter.painting.gpu_strokes import UnsupportedSurface
patch.object(EngineGraph, '_strokes_boundary', Graph._strokes_boundary).start()
patch('open_painter.painting.gpu_strokes.Canvas', side_effect=UnsupportedSurface('CPU compatibility test')).start()
patch('open_painter.evaluation.engine.filters.supported', return_value=False).start()
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
    settings.reach = "SURFACE"          # the default of the brush: the surface under the pointer
    settings.channels = {"BASE_COLOR", "ROUGHNESS", "METALLIC", "HEIGHT"}
    settings.base_color, settings.roughness, settings.metallic, settings.height = (.55, .25, .1, 1), .8, 0.0, .6
    for key, value in values.items():
        setattr(settings, key, value)


def new_gesture(ts, layer, view, preview=True, universal=None):
    scope = gesture.Scope.of(project, ts, across_sets=bool(settings.universal if universal is None else universal))
    return gesture.Gesture(scene, ts, layer, settings, scope, view, preview=preview)


def straight(rgba):
    return rgba[..., :3]


def texels_of(ts, polygons, n, object_name=None):
    """(n, n) bool: the texels of the given evaluated polygons of the set's objects."""
    from open_painter.geometry.snapshots import targets
    from open_painter.geometry.uv_rasterizer import rasterize
    mask = np.zeros((n, n), bool)
    wanted = np.asarray(list(polygons), np.int64)
    with targets(ts) as entries:
        for target in entries:
            if object_name and target["object"].name != object_name:
                continue
            chosen = np.isin(target["tri_polygons"], wanted)
            if not chosen.any():
                continue
            rows, cols, _, _ = rasterize(target["uv"][target["tri_loops"][chosen]], n)
            mask[rows, cols] = True
    return mask


def digest(arrays):
    return {key: hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest() for key, value in arrays.items()}


def export_maps(ts, folder):
    """Export the set (no padding, 8-bit PNG) and read the files back: name -> pixels."""
    from open_painter.export.exporter import export
    ts.export.padding_mode, ts.export.padding_pixels = "NONE", 0
    ts.export.resolution, ts.export.preset, ts.export.file_format, ts.export.bit_depth = "SET", "BLENDER", "PNG", "8"
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    result = {}
    for path in export(bpy.context, ts, ts.export, directory=folder, overwrite=True):
        image = bpy.data.images.load(str(path))
        result[path.name] = images.pixels(image).copy()
        bpy.data.images.remove(image)
    return result


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
# Released: each node binds its persistent leaf again, and the committed stroke regenerates into
# that leaf in place (0.13 stability S2); the preview images are reaped once nothing binds them.
graph = shader_preview.ensure(scene, plate)
assert not any(getattr(node, "image", None) is not None and node.image.get("op_gesture_preview")
               for node in graph.nodes()), "a released preview is still bound"
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
# A same-facing sheet behind the one under the pointer: the plate lies 8 mm above the box's top
# rim, both in the set, both facing up, disconnected pieces of one object. In the default Surface
# reach the rim beneath stays clean whatever the depth band says (the plate is the surface the
# pointer hit; the rim is another piece); the explicit Depth Band reach — what strokes recorded
# before 0.13.0 keep — reaches the rim once the band (depth limit × radius) exceeds the 8 mm gap.
rim = [p.index for p in scene.objects[parts["assigned"]].data.polygons
       if p.material_index == 0 and p.normal.z > .9 and .95 < p.center.z < 1.005]
assert rim, "the fixture needs the box's top rim"
rim_texels = texels_of(isolation, rim, 512, object_name=parts["assigned"])
assert rim_texels.any()
configure(radius=.1, spacing=.25, depth_limit=1.0)                   # Surface reach, band 10 cm
g = new_gesture(isolation, iso_layer, view, preview=False)
stroke_surface = drag(g, [(65, -30), (95, 30)], events=6)
configure(radius=.1, spacing=.25, depth_limit=.05, reach="DEPTH")    # band 5 mm: the rim stays clean
g = new_gesture(isolation, iso_layer, view, preview=False)
stroke_tight = drag(g, [(65, -30), (95, 30)], events=6)
configure(radius=.1, spacing=.25, depth_limit=1.0, reach="DEPTH")    # band 10 cm: the rim receives paint
g = new_gesture(isolation, iso_layer, view, preview=False)
stroke_loose = drag(g, [(65, -30), (95, 30)], events=6)
assert stroke_surface.reach == "SURFACE" and stroke_tight.reach == stroke_loose.reach == "DEPTH"
assert len(surface_reach.unpack_anchors(stroke_surface.anchors)) == stroke_surface.sample_count > 0
surface = stroke_raster.coverage(isolation, stroke_surface, 512)
tight = stroke_raster.coverage(isolation, stroke_tight, 512)
loose = stroke_raster.coverage(isolation, stroke_loose, 512)
assert positions[:, 2].max() > .9
assert (surface > .5).sum() > 100 and surface[rim_texels].max() == 0.0, ((surface > .5).sum(), surface[rim_texels].max())
assert tight[rim_texels].max() == 0.0
assert loose[rim_texels].max() > .5, "the explicit depth band must show its broader reach"
assert np.array_equal(surface, tight), "on the plate itself the Surface reach paints what the tight band paints"
check("the rear sheet 8 mm below stays clean in the default reach; the depth band reaches it on request",
      plate_texels=int((surface > .5).sum()), rim_texels_hit_by_depth_band=int((loose[rim_texels] > .5).sum()),
      rim_texels_hit_by_surface_reach=int((surface[rim_texels] > 0).sum()))

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
# A broad stroke along the plate's right edge: its discs spill over the 0.2 gap onto the right
# plate but the pointer never crosses it. In the default Surface reach the neighbour (another
# object) is not even marked; with the explicit Depth Band reach the raster alone would mark it,
# and still the set does not link: the pointer decides, not the disc.
g = new_gesture(plate, mud, view, universal=True, preview=False)
stroke = drag(g, [(96, -60), (96, 60)], events=8)
assert g.hit_sets == {plate.id}, g.hit_sets
assert not any(l.stroke_key == mud.stroke_key for l in right_set.layers), "a set the pointer never crossed must not link"
assert not stroke_raster.touches(right_set, stroke, 512), "the Surface reach must not spill onto another object"
assert records.scope_ids(stroke) == {plate.id} and stroke.scope_version == records.SCOPE_VERSION
records.remove(project, stroke.id)
configure(radius=.3, spacing=.25, universal=True, reach="DEPTH")
g = new_gesture(plate, mud, view, universal=True, preview=False)
stroke = drag(g, [(96, -60), (96, 60)], events=8)
assert stroke_raster.touches(right_set, stroke, 512), "the depth band alone would have marked the neighbour"
assert not any(l.stroke_key == mud.stroke_key for l in right_set.layers), "a marked but uncrossed set must not link"
assert records.scope_ids(stroke) == {plate.id}
records.remove(project, stroke.id)
configure(radius=.3, spacing=.25, universal=True)
# Crossing onto it with cross-set painting on: it links, and the stroke's scope names both sets.
right_before = outputs(right_set)
g = new_gesture(plate, mud, view, universal=True, preview=False)
crossing = drag(g, [(60, 0), (200, 0)], events=10)
linked = [l for l in right_set.layers if l.stroke_key == mud.stroke_key]
assert len(linked) == 1, "cross-set painting links the set the pointer crossed"
assert records.scope_ids(crossing) == {plate.id, right_set.id}, records.scope_ids(crossing)
right_linked = outputs(right_set)
assert not np.array_equal(right_linked["BASE_COLOR"], right_before["BASE_COLOR"]), "the crossed set renders the stroke"
check("cross-set painting links and permits only the sets the pointer crossed")

# With cross-set painting off again, the link stays but grants nothing: a single-set stroke
# whose disc spills onto the right plate lifts at the set's edge, its scope names the plate alone,
# and the right set's actual channel pixels stay byte-identical — in the CPU evaluation, in the
# live shader's stroke identity, in a full recompute at another resolution and in the export.
plate_before = outputs(plate)
configure(radius=.3, spacing=.25, universal=False, reach="DEPTH")   # the broadest reach: the disc does spill
g = new_gesture(plate, mud, view, universal=False, preview=False)
single = drag(g, [(60, 0), (200, 0)], events=10)
samples = stroke_raster.unpack_samples(single.samples)
assert samples[:, 0].max() < 1.05, "without cross-set painting the stroke lifts at the set's edge"
assert len([l for l in right_set.layers if l.stroke_key == mud.stroke_key]) == 1, "an existing link is kept"
assert records.scope_ids(single) == {plate.id}, records.scope_ids(single)
assert stroke_raster.touches(right_set, single, 512), "the disc reaches the right plate: only the scope protects it"
right_after = outputs(right_set)
assert digest(right_after) == digest(right_linked), "the excluded set's pixels changed"
assert not np.array_equal(outputs(plate)["BASE_COLOR"], plate_before["BASE_COLOR"]), "the stroke renders on its own set"
assert single.id in {s.id for s in records.strokes_for(project, mud.stroke_key, plate)}
assert single.id not in {s.id for s in records.strokes_for(project, mud.stroke_key, right_set)}
assert (single.id, single.revision) not in records.stroke_identity(project, mud.stroke_key, right_set)
assert (single.id, single.revision) in records.stroke_identity(project, mud.stroke_key, plate)
# The same holds for an erase stroke.
configure(radius=.3, spacing=.25, universal=False, blend="ERASE", reach="DEPTH")
g = new_gesture(plate, mud, view, universal=False, preview=False)
erase = drag(g, [(60, 0), (200, 0)], events=10)
assert records.scope_ids(erase) == {plate.id} and stroke_raster.touches(right_set, erase, 512)
assert digest(outputs(right_set)) == digest(right_linked), "an erase stroke widened its scope"
# A full recompute at another resolution renders the permitted strokes only.
right_set.resolution = "1024"
low = outputs(right_set)
permitted = {s.id for s in records.strokes_for(project, mud.stroke_key, right_set)}
assert permitted == {crossing.id}, permitted
hidden = [s for s in records.strokes_for(project, mud.stroke_key) if s.id not in permitted]
assert hidden, "the single-set strokes are the excluded ones"
saved_scopes = {s.id: (s.scope, s.scope_version) for s in hidden}
for stroke in hidden:      # widen by hand: the same pixels must then change (the test sees the difference)
    stroke.scope = ",".join(sorted(records.scope_ids(stroke) | {right_set.id}))
assert digest(outputs(right_set)) != digest(low), "widening the scope must reach the set (the control)"
for stroke in hidden:
    stroke.scope, stroke.scope_version = saved_scopes[stroke.id]
assert digest(outputs(right_set)) == digest(low)
right_set.resolution = "512"
assert digest(outputs(right_set)) == digest(right_linked)
# The export reads the same evaluation: the excluded strokes are absent from the files.
with tempfile.TemporaryDirectory(prefix="op-scope-export-") as tmp:
    exported = export_maps(right_set, Path(tmp) / "after")
    for stroke in hidden:
        stroke.scope = ",".join(sorted(records.scope_ids(stroke) | {right_set.id}))
    widened = export_maps(right_set, Path(tmp) / "widened")
    for stroke in hidden:
        stroke.scope, stroke.scope_version = saved_scopes[stroke.id]
    again = export_maps(right_set, Path(tmp) / "again")
    assert digest(exported) == digest(again), "the export must render the permitted strokes only"
    assert digest(exported) != digest(widened), "the widened control must differ"
check("a single-set stroke never alters a set an earlier cross-set stroke linked",
      excluded_strokes=len(hidden), right_set_digest=digest(right_linked)["BASE_COLOR"][:16])

# Cross-set painting on again, crossing onto the right plate on purpose: both permitted sets render
# the new stroke, and the earlier single-set strokes stay where they were authorised.
configure(radius=.3, spacing=.25, universal=True)
g = new_gesture(plate, mud, view, universal=True, preview=False)
second_crossing = drag(g, [(60, 40), (200, 40)], events=10)
assert records.scope_ids(second_crossing) == {plate.id, right_set.id}
assert g.hit_sets == {plate.id, right_set.id}
right_now = outputs(right_set)
assert not np.array_equal(right_now["BASE_COLOR"], right_linked["BASE_COLOR"]), "the crossed set must render the new stroke"
assert {s.id for s in records.strokes_for(project, mud.stroke_key, right_set)} == {crossing.id, second_crossing.id}
records.remove(project, second_crossing.id)
assert digest(outputs(right_set)) == digest(right_linked)
for stroke_id in (single.id, erase.id):
    records.remove(project, stroke_id)
check("enabling cross-set painting again permits exactly the sets the gesture crosses")
# The symmetric case: a single-set stroke painted on the right set's linked layer (the same key)
# stays the right set's; its disc reaches the plate, the plate's pixels do not change.
right_mud = next(l for l in right_set.layers if l.stroke_key == mud.stroke_key)
right_set.active_layer = stack.index_of(right_set, right_mud.id)
plate_digest_before = digest(outputs(plate))
configure(radius=.3, spacing=.25, universal=False, reach="DEPTH")
g = new_gesture(right_set, right_mud, view, universal=False, preview=False)
from_right = drag(g, [(200, -40), (118, -40)], events=8)   # lifts in the gap past x = 1.2
assert from_right.layer_key == mud.stroke_key and records.scope_ids(from_right) == {right_set.id}
assert stroke_raster.touches(plate, from_right, 512), "the disc reaches the plate: only the scope protects it"
assert digest(outputs(plate)) == plate_digest_before, "the plate rendered a stroke scoped to the right set"
assert from_right.id not in {s.id for s in records.strokes_for(project, mud.stroke_key, plate)}
plate.active_layer = stack.index_of(plate, mud.id)
check("shared history stays where it was authorised: a stroke from the linked set never reaches the plate")

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

# ---- 11. an occluded recess in one connected surface; two nearby same-facing objects ------------------
shelf, shelf_parts = fixtures.shelf(scene, resolution=512, location=(40, 0, 0))
shelf_layer = stack.add(shelf, "PAINT", "Shelf marks")
shelf.active_layer = stack.index_of(shelf, shelf_layer.id)
n_shelf = 512
hidden_floor = texels_of(shelf, [shelf_parts["hidden_floor"]], n_shelf)
open_floor = texels_of(shelf, [shelf_parts["open_floor"]], n_shelf)
lip = texels_of(shelf, [shelf_parts["lip"]], n_shelf)
assert hidden_floor.any() and open_floor.any() and lip.any()
view = TopView(scale=100.0, centre=(40, 0, 0))
# A stamp on the lip above the pocket, with a band deep enough (radius .3 × 1.0 > the .2 drop) to
# reach the floor beneath: the Surface reach stops at the wall, the Depth Band reach does not.
configure(radius=.3, spacing=.25, depth_limit=1.0)
g = new_gesture(shelf, shelf_layer, view, preview=False)
on_lip = drag(g, [(20, -30), (40, 30)], events=6)
configure(radius=.3, spacing=.25, depth_limit=1.0, reach="DEPTH")
g = new_gesture(shelf, shelf_layer, view, preview=False)
on_lip_depth = drag(g, [(20, -30), (40, 30)], events=6)
cov_surface = stroke_raster.coverage(shelf, on_lip, n_shelf)
cov_depth = stroke_raster.coverage(shelf, on_lip_depth, n_shelf)
assert cov_surface[lip].max() > .5 and cov_depth[lip].max() > .5
assert cov_surface[hidden_floor].max() == 0.0, "the floor under the lip must stay clean in the default reach"
assert cov_depth[hidden_floor].max() > .5, "the depth band reaches the hidden floor: its broader reach is explicit"
# Painting the open floor continues under the lip: that floor is one connected, same-facing surface
# (the documented reach of Surface: what connects without turning away), unlike the lip above it.
configure(radius=.3, spacing=.25, depth_limit=1.0)
g = new_gesture(shelf, shelf_layer, view, preview=False)
on_floor = drag(g, [(80, -30), (90, 30)], events=6)
cov_floor = stroke_raster.coverage(shelf, on_floor, n_shelf)
assert cov_floor[open_floor].max() > .5 and cov_floor[hidden_floor].max() > .5
assert cov_floor[lip].max() == 0.0, "the lip above the floor is not reached from the floor"
check("an occluded recess stays clean in the default reach", lip=int((cov_surface > .5).sum()),
      hidden_floor_depth_band=int((cov_depth[hidden_floor] > .5).sum()))
# Two nearby same-facing objects in one set: a stamp on one never crosses the 2 mm gap to the other.
near_a = fixtures._mesh_object("Near A", [(-1, -1, 0), (0, -1, 0), (0, 1, 0), (-1, 1, 0)], [(0, 1, 2, 3)],
                               uvs=[[(0, 0), (.48, 0), (.48, 1), (0, 1)]], location=(50, 0, 0))
near_b = fixtures._mesh_object("Near B", [(.002, -1, 0), (1, -1, 0), (1, 1, 0), (.002, 1, 0)], [(0, 1, 2, 3)],
                               uvs=[[(.52, 0), (1, 0), (1, 1), (.52, 1)]], location=(50, 0, 0))
near_set = fixtures._set(scene, "Near", near_a, 512)
sets.assign(scene, near_set, near_b)
bpy.context.view_layer.update()
near_layer = stack.add(near_set, "PAINT", "Near marks")
near_set.active_layer = stack.index_of(near_set, near_layer.id)
view = TopView(scale=100.0, centre=(50, 0, 0))
configure(radius=.3, spacing=.25, depth_limit=1.0)
g = new_gesture(near_set, near_layer, view, preview=False)
on_a = drag(g, [(-10, -50), (-10, 50)], events=6)      # x = -.1: the disc spans the gap onto B
configure(radius=.3, spacing=.25, depth_limit=1.0, reach="DEPTH")
g = new_gesture(near_set, near_layer, view, preview=False)
on_a_depth = drag(g, [(-10, -50), (-10, 50)], events=6)
b_texels = texels_of(near_set, [0], 512, object_name="Near B")
a_texels = texels_of(near_set, [0], 512, object_name="Near A")
cov_a = stroke_raster.coverage(near_set, on_a, 512)
cov_a_depth = stroke_raster.coverage(near_set, on_a_depth, 512)
assert cov_a[a_texels].max() > .5 and cov_a[b_texels].max() == 0.0, "the neighbouring object must stay clean"
assert cov_a_depth[b_texels].max() > .5, "the depth band reaches the neighbour"
check("two nearby same-facing objects: the stamp stays on the one under the pointer",
      b_texels_hit_by_depth_band=int((cov_a_depth[b_texels] > .5).sum()))

# ---- 12. legacy strokes keep their reach and render wherever their key is shared --------------------------
from types import SimpleNamespace
legacy_settings = SimpleNamespace(radius=.3, hardness=.8, opacity=1.0, flow=1.0, accumulate="MAX", alpha_image=None,
                                  projection="SURFACE", blend="PAINT", base_color=(.2, .9, .3, 1), roughness=.4, metallic=0.0,
                                  height=.5, ao=1.0, emission=(0, 0, 0, 1), opacity_value=1.0, normal=(.5, .5, 1, 1),
                                  depth_limit=1.0, channels={"BASE_COLOR"})   # no ``reach``: a record from before 0.13.0
legacy_samples = stroke_raster.unpack_samples(on_a_depth.samples)
legacy = records.record(project, near_set, near_layer, legacy_samples, legacy_settings, universal=False)
assert legacy.reach == "DEPTH" and legacy.anchors == "", "a record without the field keeps the depth-band placement"
legacy.scope, legacy.scope_version = "", 0      # as a stroke saved before the field existed reads back
assert records.permits(legacy, near_set) and records.permits(legacy, plate), "a legacy stroke renders wherever its key is shared"
assert np.array_equal(stroke_raster.coverage(near_set, legacy, 512), cov_a_depth), "legacy placement must not change"
records.remove(project, legacy.id)
check("legacy strokes keep their placement and their reach")

# ---- 13. save/reopen reproduces the strokes; a duplicate edits independently -----------------------------
# One stroke through a generated brush alpha: the alpha image is an authored input the save
# must keep (packed as stored), or the stroke renders differently after reopening.
plate = next(t for t in project.texture_sets if t.name == "Plate")
mud = next(l for l in plate.layers if l.name == "Mud")
alpha_image = bpy.data.images.new("Replay alpha", 64, 64, alpha=True, float_buffer=True)
yy, xx = np.mgrid[0:64, 0:64]
radial = np.clip(1.0 - np.hypot(xx - 31.5, yy - 31.5) / 32.0, 0, 1).astype(np.float32)
alpha_image.pixels.foreach_set(np.repeat(radial[..., None], 4, -1).ravel())
configure(radius=.15, spacing=.25, alpha_image=alpha_image, base_color=(.9, .8, .1, 1))
g = new_gesture(plate, mud, TopView(scale=200.0), preview=False)
alpha_stroke = drag(g, [(-100, 60), (100, 60)], events=8)
assert alpha_stroke.alpha_image == alpha_image
alpha_stroke_id = alpha_stroke.id      # the RNA reference dies with the reopen
configure(radius=.15, spacing=.25)
final = outputs(plate)
final_digest = {c: hashlib.sha256(v.tobytes()).hexdigest() for c, v in final.items()}
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
    assert {c: hashlib.sha256(v.tobytes()).hexdigest() for c, v in reopened.items()} == final_digest, "reopened strokes differ"
    kept_alpha = bpy.data.images.get("Replay alpha")
    assert kept_alpha is not None and kept_alpha.packed_file is not None and kept_alpha.alpha_mode == "CHANNEL_PACKED"
    assert next(s for s in project.strokes if s.id == alpha_stroke_id).alpha_image == kept_alpha
    check("save and reopen reproduce the strokes", alpha_packed=True)
    mud_key = mud.stroke_key
    copy = stack.duplicate(plate, stack.index_of(plate, mud.id))
    copy_key = copy.stroke_key
    mud = next(l for l in plate.layers if l.name == "Mud")
    assert copy_key and copy_key != mud_key
    # The copy carries the strokes the plate may render (the right set's own stroke stays behind).
    assert len(records.strokes_for(project, copy_key)) == len(records.strokes_for(project, mud_key, plate))
    assert len(records.strokes_for(project, mud_key, plate)) < len(records.strokes_for(project, mud_key))
    configure(radius=.1, spacing=.25, base_color=(.1, .9, .2, 1))
    g = new_gesture(plate, copy, TopView(scale=200.0), preview=False)
    stroke = drag(g, [(-100, 0), (100, 0)], events=8)
    assert stroke.layer_key == copy.stroke_key
    assert all(s.layer_key != mud.stroke_key or s.id != stroke.id for s in project.strokes)
    assert len(records.strokes_for(project, mud.stroke_key, plate)) + 1 == len(records.strokes_for(project, copy.stroke_key))
    # The copies are scoped to the set they live in: none names another set, none is visible elsewhere.
    copies = records.strokes_for(project, copy.stroke_key)
    assert all(records.scope_ids(s) == {plate.id} and s.scope_version == records.SCOPE_VERSION for s in copies), \
        [(s.name, s.scope) for s in copies]
    assert all(s.reach == o.reach and s.anchors == o.anchors for s, o in zip(copies[:-1], records.strokes_for(project, mud_key, plate)))
    check("duplicates edit independently")
    # Reopened scopes: every stroke of the plate's key still names the sets it did (the file keeps
    # the field), and a stroke's anchors and reach survive too.
    reopened_scopes = {s.id: (records.scope_ids(s), s.reach, len(s.anchors)) for s in records.strokes_for(project, mud_key)}
    assert reopened_scopes and all(scope for scope, _, _ in reopened_scopes.values()), reopened_scopes
    right_set = next(t for t in project.texture_sets if t.name == "Right")
    right_reopened = outputs(right_set)
    assert {s.id for s in records.strokes_for(project, mud_key, right_set)} == {s.id for s in records.strokes_for(project, mud_key) if right_set.id in records.scope_ids(s)}
    # A duplicated texture set renders the same as its source (what the source may render) under keys
    # of its own, scoped to the copy: painting on either afterwards leaves the other alone.
    plate_index = next(i for i, t in enumerate(project.texture_sets) if t.id == plate.id)
    plate_digest = digest(outputs(plate))
    twin = sets.duplicate(scene, plate_index)
    plate = next(t for t in project.texture_sets if t.name == "Plate")
    twin = next(t for t in project.texture_sets if t.id == twin.id)
    twin_obj = next(ref.object for ref in plate.assignments).copy()
    twin_obj.data = twin_obj.data.copy()
    scene.collection.objects.link(twin_obj)
    sets.assign(scene, twin, twin_obj)
    for ref in list(twin.assignments):
        if ref.object != twin_obj:
            twin.assignments.remove(list(twin.assignments).index(ref))
    bpy.context.view_layer.update()
    twin_mud = next(l for l in twin.layers if l.name == "Mud")
    assert twin_mud.stroke_key and twin_mud.stroke_key != mud_key, "the copy must not share the source's key"
    twin_strokes = records.strokes_for(project, twin_mud.stroke_key)
    assert twin_strokes and all(records.scope_ids(s) == {twin.id} for s in twin_strokes), [(s.name, s.scope) for s in twin_strokes]
    assert len(twin_strokes) == len(records.strokes_for(project, mud_key, plate))
    assert digest(outputs(twin)) == plate_digest, "the copy must render what its source renders"
    # The twin moves beside its source (the ray would hit the source first where they overlap).
    twin_obj.location.x += 60
    bpy.context.view_layer.update()
    configure(radius=.1, spacing=.25, base_color=(.9, .1, .1, 1))
    twin.active_layer = stack.index_of(twin, twin_mud.id)
    g = new_gesture(twin, twin_mud, TopView(scale=200.0, centre=(60, 0, 0)), preview=False)
    on_twin = drag(g, [(-100, -40), (100, -40)], events=8)
    assert on_twin.layer_key == twin_mud.stroke_key and records.scope_ids(on_twin) == {twin.id}
    assert digest(outputs(plate)) == plate_digest, "painting the copy must leave the source alone"
    right_set = next(t for t in project.texture_sets if t.name == "Right")   # the collection grew: re-resolved
    assert digest(outputs(right_set)) == digest(right_reopened)
    check("a duplicated set renders the same and paints independently")
    # A portable material document carries the strokes the saved set may render; the import scopes
    # the copies to the importing set alone (no set of the saving project is named).
    from open_painter.smart_materials import loader, serializer
    import json
    from zipfile import ZipFile
    fresh_obj = twin_obj.copy(); fresh_obj.data = twin_obj.data.copy(); scene.collection.objects.link(fresh_obj)
    fresh_obj.location.x -= 60      # back over the source: the imported strokes are world-space records
    fresh = sets.create(scene, "Fresh", 512); sets.assign(scene, fresh, fresh_obj)
    bpy.context.view_layer.update()
    document_path = Path(tmp) / "mud.openpaintmaterial"
    serializer.save(plate, document_path)
    with ZipFile(document_path) as archive:
        document = json.loads(archive.read("material.json"))
    saved = [s for s in document.get("strokes", []) if s.get("layer_key") == mud_key]
    assert saved and len(saved) == len(records.strokes_for(project, mud_key, plate)), "only the strokes the set renders travel"
    assert len(saved) < len(records.strokes_for(project, mud_key)), "the strokes the set may not render stay behind"
    fresh_id = fresh.id
    loader.apply(fresh, document_path)
    fresh = next(t for t in project.texture_sets if t.id == fresh_id)
    plate = next(t for t in project.texture_sets if t.name == "Plate")
    imported = next(l for l in fresh.layers if l.name == "Mud")
    imported_strokes = records.strokes_for(project, imported.stroke_key)
    assert len(imported_strokes) == len(saved)
    assert all(records.scope_ids(s) == {fresh.id} and s.scope_version == records.SCOPE_VERSION for s in imported_strokes)
    assert all(s.reach == o.reach and s.anchors == o.anchors for s, o in zip(imported_strokes, records.strokes_for(project, mud_key, plate)))
    # The document carries the brush alpha as an EXR asset: a straight-alpha float file is
    # associated on load and divided back on read, so the alpha stroke differs by float noise
    # only (the saved project itself reopens bit-identically, checked above).
    imported_outputs = outputs(fresh)
    source_outputs = outputs(plate)
    import_error = {c: float(np.abs(imported_outputs[c] - source_outputs[c]).max()) for c in source_outputs}
    assert max(import_error.values()) < 1e-4, ("the import must render what the source renders", import_error)
    check("imported strokes are scoped to the importing set", strokes=len(imported_strokes), max_error=import_error)

REPORT.write_text(json.dumps(report, indent=2))
print("MATERIAL_PAINTING_PASS", REPORT, flush=True)
