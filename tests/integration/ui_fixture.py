"""Regression fixture for the UI refactor (Phase 0 of the UI implementation plan).

Importable, not a test: ``build(scene)`` populates the scene deterministically with
everything the interface refactor must keep identical — a Worn Painted Metal set
with baked maps, nested folders, a paint layer with a painted mask, several mask
sources including a custom graph, a Material Aging folder, a geometry effect, a
second set with export overrides — and ``record(scene, ids)`` reads back the
document digests, layer structure, CPU-evaluated channel statistics and the
resolved export outputs. ``build_large(scene)`` adds the stack used for timing.

Everything is fixed-seed; the baseline JSON is compared, not regenerated, by
``ui_baseline.py``.
"""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import bpy
import numpy as np

from open_painter.core import image_manager as images, layer_stack as stack, texture_set_manager as sets

ROOT = Path(__file__).resolve().parents[2]
PREVIEW = 64


def bake_synthetic(ts, side=32):
    """The four analytic mesh maps the catalog, smart masks and aging read."""
    u, v = np.meshgrid((np.arange(side) + .5) / side, (np.arange(side) + .5) / side)
    for kind in ("CURVATURE", "AO", "POSITION", "WORLD_NORMAL"):
        entry = ts.baked_maps.add()
        entry.kind = kind
        entry.image = images.create(f"{ts.name} {kind}", side)
        value = np.ones((side, side, 4), np.float32)
        if kind == "CURVATURE":
            value[..., :3] = np.clip((.16 - np.abs(u - .5)) * 8, 0, 1)[..., None]
        elif kind == "AO":
            value[..., :3] = (.25 + .75 * v)[..., None]
        elif kind == "POSITION":
            value[..., 0], value[..., 1], value[..., 2] = u, v, v
        else:
            z = 2 * v - 1
            value[..., 0] = .5 + .5 * np.sqrt(np.maximum(0, 1 - z * z))
            value[..., 1], value[..., 2] = .5, v
        images.write(entry.image, value)


def _second_mesh(scene, name="Panel"):
    obj = scene.objects.get(name)
    if obj is not None:
        return obj
    cube = scene.objects["Cube"]
    mesh = cube.data.copy()
    mesh.name = name
    obj = bpy.data.objects.new(name, mesh)
    obj.location.x += 3
    scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj


def _painted(name, side, seed):
    """A deterministic painted mask/canvas: a soft disc offset by the seed."""
    u, v = np.meshgrid((np.arange(side) + .5) / side, (np.arange(side) + .5) / side)
    value = np.ones((side, side, 4), np.float32)
    disc = np.clip(1.5 - 6 * np.hypot(u - .35 - .1 * seed, v - .55), 0, 1)
    value[..., :3] = disc[..., None]
    image = images.create(name, side)
    images.write(image, value, pack=True)
    return image


def build(scene, *, resolution="512"):
    """Populate ``scene``; returns the ids the tests refer to."""
    from open_painter.aging import engine
    from open_painter.geometry_effects import manager
    from open_painter.smart_materials import composer
    from open_painter.ui.properties_panel import starter_graph
    from open_painter.utils.ids import new_id

    project = scene.open_painter
    cube = scene.objects["Cube"]
    panel = _second_mesh(scene)
    ids = {}

    body = sets.create(scene, "Body", int(resolution))
    sets.assign(scene, body, cube)
    body.preview_backend = "CPU"
    bake_synthetic(body)
    ids["body"] = body.id
    material = composer.apply(body, "Worn Painted Metal", seed=3)
    ids["material"] = material.id
    ids["material_layers"] = {layer.name: layer.id for layer in body.layers if layer.parent_id == material.id}

    # Nested folders: Details › Hand Paint (paint layer, painted mask) and Stripes (procedural fill).
    body.active_layer = stack.index_of(body, ids["material"])
    details = stack.add(body, "FOLDER", "Details")
    details_id = details.id
    stripes = stack.add(body, "FILL", "Stripes", parent_id=details_id)
    stripes_id = stripes.id
    stripes = next(item for item in body.layers if item.id == stripes_id)
    base = stack.channel_for(stripes, "BASE_COLOR")
    base.procedural, base.procedural_scale, base.procedural_seed = "STRIPES", 6, 4
    base.color = (.8, .7, .2, 1)
    stack.channel_for(stripes, "ROUGHNESS").value = .6
    stripes.opacity = .8
    paint = stack.add(body, "PAINT", "Hand Paint", parent_id=details_id)
    paint_id = paint.id
    paint = next(item for item in body.layers if item.id == paint_id)
    prop = stack.channel_for(paint, "BASE_COLOR")
    prop.enabled = True
    prop.image = _painted("Body Hand Paint", 64, 0)
    prop.image.colorspace_settings.name = "Non-Color"
    mask = paint.masks.add()
    mask.id, mask.name, mask.source = new_id(), "Painted Mask", "PAINT"
    mask.image = _painted("Body Hand Paint Mask", 64, 1)
    paint.active_mask = 0
    ids.update(details=details_id, stripes=stripes_id, paint=paint_id, painted_mask=mask.id)

    # Several mask sources on one fill layer, including a disabled custom graph.
    body.active_layer = stack.index_of(body, details_id)
    grime = stack.add(body, "FILL", "Grime")
    grime_id = grime.id
    grime = next(item for item in body.layers if item.id == grime_id)
    stack.channel_for(grime, "BASE_COLOR").color = (.05, .04, .03, 1)
    stack.channel_for(grime, "ROUGHNESS").value = .9
    grime.opacity = .7
    sources = []
    for name, source, extra in (
        ("Cavity", "GENERATOR", dict(generator="CAVITY_DIRT", amount=.4, contrast=3, seed=5)),
        ("Speckle", "NOISE", dict(operation="MULTIPLY", noise_scale=9, noise_strength=.5, seed=6)),
        ("Grunge", "PROCEDURAL", dict(operation="MULTIPLY", procedural="GRUNGE", noise_scale=7, seed=7)),
        ("Occlusion", "BAKED", dict(operation="MULTIPLY", map_type="AO", invert=True)),
        ("Levels", "FILTER", dict()),
        ("Custom Graph", "GRAPH", dict(enabled=False)),
    ):
        entry = grime.masks.add()
        entry.id, entry.name, entry.source = new_id(), name, source
        if source == "FILTER":
            entry.effect.kind = "LEVELS"
            entry.black, entry.white = .1, .9
        if source == "GRAPH":
            entry.node_group = starter_graph("Body Grime Mask Graph")
        for key, value in extra.items():
            setattr(entry, key, value)
        sources.append(entry.id)
    grime.active_mask = 0
    ids.update(grime=grime_id, grime_masks=sources)

    # Material Aging folder driven by the simulation (AO and Curvature are present).
    body.aging.age_years = 6
    body.aging.seed = 11
    engine.apply(body)
    aged = engine.folder_of(body)
    ids["aging_folder"] = aged.id if aged is not None else ""

    # One geometry effect helper placed by a small density image.
    density = images.create("Body Bolt Density", 32, (1, 1, 1, 1))
    helper = manager.create(body, cube, density, "BOLT", 4, .05, 2)
    ids["geometry_helper"] = helper.name if helper is not None else ""

    # A second set on another object with its own export overrides.
    panel_set = sets.create(scene, "Panel", 512)
    sets.assign(scene, panel_set, panel)
    panel_set.preview_backend = "CPU"
    fill = stack.add(panel_set, "FILL", "Panel Base")
    stack.channel_for(fill, "BASE_COLOR").color = (.2, .3, .5, 1)
    stack.channel_for(fill, "ROUGHNESS").value = .35
    stack.channel_for(fill, "METALLIC").value = 1
    panel_set.export.follow_target = False
    panel_set.export.preset = "UNITY"
    panel_set.export.prefix = "P_"
    panel_set.export.directory = "//exports/panel"
    panel_set.export.bit_depth = "16"
    panel_set.export.file_format = "TIFF"
    ids["panel"] = panel_set.id

    project.target = "UNREAL"
    project.export_directory = "//exports"
    project.active_texture_set = 0
    body = next(item for item in project.texture_sets if item.id == ids["body"])
    body.active_layer = stack.index_of(body, ids["material"])
    return ids


def build_large(scene, layers=48, resolution="512"):
    """A tall stack for timing: fills with procedurals and generator masks."""
    from open_painter.utils.ids import new_id
    cube = scene.objects["Cube"]
    ts = sets.create(scene, "Large", int(resolution))
    sets.assign(scene, ts, cube)
    bake_synthetic(ts)
    folder = None
    for index in range(layers):
        if index % 8 == 0:
            folder = stack.add(ts, "FOLDER", f"Group {index // 8}")
            folder_id = folder.id
        layer = stack.add(ts, "FILL", f"Fill {index}", parent_id=folder_id)
        layer_id = layer.id
        layer = next(item for item in ts.layers if item.id == layer_id)
        base = stack.channel_for(layer, "BASE_COLOR")
        base.color = ((index % 5) / 5, (index % 3) / 3, (index % 7) / 7, 1)
        if index % 2:
            base.procedural, base.procedural_seed = "FBM", index
        mask = layer.masks.add()
        mask.id, mask.name = new_id(), "Wear"
        mask.source, mask.generator, mask.seed = "GENERATOR", ("EDGE_WEAR", "DIRT", "CAVITY_DIRT")[index % 3], index
        mask.amount = .3 + .05 * (index % 5)
    return ts


def _pixels(scene, ts, channel):
    from open_painter.evaluation.evaluator import LayerEvaluator
    steps = LayerEvaluator(scene).channel_steps(ts.id, channel, preview_resolution=PREVIEW)
    try:
        while True:
            next(steps)
    except StopIteration as done:
        return images.pixels(done.value)[..., :3].copy()


def structure(ts):
    names = {layer.id: layer.name for layer in ts.layers}
    # Parents by name: ids are minted per build, the hierarchy is what must hold.
    return [dict(name=layer.name, type=layer.type, parent=names.get(layer.parent_id, layer.parent_id), id=layer.id,
                 opacity=round(layer.opacity, 5), blend=layer.blend_mode, group=layer.group_mode,
                 masks=[(m.name, m.source, m.operation, round(m.amount, 5), round(m.strength, 5)) for m in layer.masks],
                 channels=[(c.channel, c.enabled, c.blend_mode, round(c.opacity, 5)) for c in layer.channels])
            for layer in ts.layers]


def channel_stats(scene, ts):
    from open_painter.constants import CHANNELS
    result = {}
    for channel in CHANNELS:
        value = _pixels(scene, ts, channel)
        quantised = np.clip(np.round(value * 255), 0, 255).astype(np.uint8)
        result[channel] = dict(mean=[float(x) for x in value.reshape(-1, 3).mean(axis=0)],
                               std=[float(x) for x in value.reshape(-1, 3).std(axis=0)],
                               hash=hashlib.sha256(quantised.tobytes()).hexdigest()[:16])
    return result


def export_outputs(context, ts):
    """File names and mean pixel of the set's texture export, through the set's own settings."""
    from open_painter.export.exporter import export
    result = []
    with TemporaryDirectory(prefix="op-ui-baseline-") as directory:
        for path in export(context, ts, ts.export, directory=directory):
            image = bpy.data.images.load(str(path))
            image.colorspace_settings.name = "Non-Color"
            value = images.pixels(image)
            result.append(dict(name=path.name, size=list(image.size),
                               mean=[float(x) for x in value.reshape(-1, 4).mean(axis=0)]))
            bpy.data.images.remove(image)
    return result


def record(context, ids):
    """Everything the refactor must keep: digests, structure, evaluated pixels, export outputs."""
    from open_painter.authoring import document
    scene = context.scene
    project = scene.open_painter
    result = {"sets": {}}
    for key in ("body", "panel"):
        ts = next(item for item in project.texture_sets if item.id == ids[key])
        result["sets"][key] = dict(name=ts.name, resolution=ts.resolution, digest=document.digest(ts),
                                   structure=structure(ts), channels=channel_stats(scene, ts),
                                   export=export_outputs(context, ts),
                                   export_settings={k: getattr(ts.export, k) for k in
                                                    ("directory", "preset", "prefix", "suffix", "file_format",
                                                     "bit_depth", "resolution", "follow_target")},
                                   aging=dict(folder=bool(ids.get("aging_folder")), years=ts.aging.age_years,
                                              material=ts.aging.material))
    result["project"] = dict(target=project.target, export_directory=project.export_directory,
                             export_mesh=project.export_mesh)
    helper = scene.objects.get(ids.get("geometry_helper", ""))
    result["geometry"] = dict(helper=bool(helper), instances=int(helper["instance_count"]) if helper else 0)
    return result


def compare(baseline, current, atol=1e-4):
    """Differences between a recorded baseline and a fresh record, empty when identical."""
    # Through JSON: a fresh record holds tuples where the saved reference holds lists.
    baseline, current = json.loads(json.dumps(baseline)), json.loads(json.dumps(current))
    problems = []
    for key, expected in baseline["sets"].items():
        actual = current["sets"].get(key)
        if actual is None:
            problems.append(f"{key}: missing set")
            continue
        for field in ("name", "resolution", "export_settings", "aging"):
            if expected[field] != actual[field]:
                problems.append(f"{key}.{field}: {expected[field]!r} != {actual[field]!r}")
        exp_struct = [{k: v for k, v in row.items() if k != "id"} for row in expected["structure"]]
        act_struct = [{k: v for k, v in row.items() if k != "id"} for row in actual["structure"]]
        if exp_struct != act_struct:
            problems.append(f"{key}.structure differs")
        for channel, stats in expected["channels"].items():
            got = actual["channels"].get(channel)
            if got is None:
                problems.append(f"{key}.{channel}: missing")
                continue
            if not np.allclose(stats["mean"], got["mean"], atol=atol) or not np.allclose(stats["std"], got["std"], atol=atol):
                problems.append(f"{key}.{channel}: mean {stats['mean']} -> {got['mean']}")
        if [row["name"] for row in expected["export"]] != [row["name"] for row in actual["export"]]:
            problems.append(f"{key}.export names: {[r['name'] for r in expected['export']]} -> {[r['name'] for r in actual['export']]}")
        else:
            for before, after in zip(expected["export"], actual["export"]):
                if before["size"] != after["size"] or not np.allclose(before["mean"], after["mean"], atol=2 / 255):
                    problems.append(f"{key}.export {before['name']}: {before['mean']} -> {after['mean']}")
    if baseline["project"] != current["project"]:
        problems.append(f"project: {baseline['project']} != {current['project']}")
    if baseline["geometry"] != current["geometry"]:
        problems.append(f"geometry: {baseline['geometry']} != {current['geometry']}")
    return problems


def load_baseline(path=ROOT / "tests/fixtures/ui_baseline.json"):
    return json.loads(path.read_text())


def save_baseline(data, path=ROOT / "tests/fixtures/ui_baseline.json"):
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
