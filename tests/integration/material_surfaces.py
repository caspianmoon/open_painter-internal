"""Metric surfaces and constrained revisions using production authoring and Eevee.

Run with Blender --background --factory-startup --python this_file.
No provider calls or artistic grades. Diagnostic meshes are not benchmark assets.
"""
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import open_painter
from open_painter.authoring import commands, document, physical_space, surfaces, transactions
from open_painter.authoring.schema import AuthoringError
from open_painter.core import texture_set_manager as sets
from open_painter.evaluation import channel_eval, material_space, shader_preview, cache
from open_painter.evaluation.evaluator import LayerEvaluator


def proposal(*cmds):
    return dict(version=1, summary="Deterministic material capability check", commands=list(cmds))


def require_rejection(call, part):
    try:
        call()
    except AuthoringError as exc:
        assert part in str(exc), (part, str(exc))
    else:
        raise AssertionError("Expected authoring refusal: " + part)


def plane(name, extent, scale=1):
    bpy.ops.mesh.primitive_plane_add(size=extent)
    obj = bpy.context.object
    obj.name = name
    obj.scale = (scale, scale, scale)
    bpy.context.view_layer.update()
    return obj


def apply(ts, cmds, op):
    return transactions.apply(ts, proposal(*cmds), operation_id=op, expected_digest=document.digest(ts))


def configure(ts, family="WOVEN_FABRIC", op="create", **values):
    result = apply(ts, [dict(op="create_surface", id="new:surface", name=family, family=family, values=values)], op)
    return result["aliases"]["new:surface"]


def fill(ts, identifier):
    return next(layer for layer in ts.layers if layer.surface_id == identifier and layer.type == "FILL")


def prop(layer, channel="BASE_COLOR"):
    return next(p for p in layer.channels if p.channel == channel)


def field(ts, owner, n=512):
    return channel_eval._procedural_tile(owner, n, 0, n, ts, {})


def run():
    open_painter.register()
    if bpy.app.timers.is_registered(cache.tick):
        bpy.app.timers.unregister(cache.tick)
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    obj = plane("Metric plane", .016)
    ts = sets.create(scene, "Metric surfaces", 512)
    ts.preview_backend = "CPU"
    sets.assign(scene, ts, obj)
    assert physical_space.capture(ts)["status"] == "UNKNOWN"
    scene.unit_settings.scale_length = .01
    assert physical_space.capture(ts)["status"] == "UNKNOWN", "Display units are not measurements"
    require_rejection(lambda: configure(ts, period_m=.001), "Physical size is unknown")
    physical_space.declare(ts, 1, "Diagnostic mesh constructed with 16mm side")
    sid = configure(ts, period_m=.001, frame="WORLD", height_m=.00002)
    original = document.snapshot(ts)
    owner = prop(fill(ts, sid))
    value = field(ts, owner)
    # 16 complete repeats over16mm;32pixels/repeat. This samples actual field,
    # not only stored parameters. Translation by1mm must preserve all samples.
    assert np.max(abs(value[:, 32:] - value[:, :-32])) < .0001
    assert np.mean(abs(value[:, 16:] - value[:, :-16])) > .02
    # Same physical surface represented by a100x world object and conversion.01.
    obj.scale = (100, 100, 100)
    bpy.context.view_layer.update()
    owner.material_meters_per_unit = .01
    scaled = field(ts, owner)
    np.testing.assert_allclose(value, scaled, atol=.0001)
    # Nonuniform object transformation encoded in world geometry exactly once.
    obj.scale = (2, .5, 3)
    bpy.context.view_layer.update()
    owner.material_meters_per_unit = 1
    stretched = field(ts, owner)
    assert np.max(abs(stretched[:, 16:] - stretched[:, :-16])) < .0001
    obj.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    document.apply(ts, original)
    print("METRIC_EVALUATED_PERIOD_OK", flush=True)

    # Recolor preserves every source, pattern, roughness, frame and coverage.
    receipt = apply(ts, [dict(op="patch_surface", surface_id=sid, mode="COLOR_ONLY", values=dict(color=[.03, .12, .04, 1]))], "recolor")
    changed = document.changes(original, document.snapshot(ts))
    assert len(changed) == 1 and changed[0][0][-1] == "color", changed
    immutable = document.snapshot(ts)
    bad = proposal(dict(op="patch_surface", surface_id=sid, mode="COLOR_ONLY", values=dict(color=[.1, .1, .1, 1])),
        dict(op="set_channel", layer_id=fill(ts, sid).id, channel="ROUGHNESS", values=dict(value=.1)))
    require_rejection(lambda: commands.compile_proposal(ts, bad, "bad"), "invariant")
    assert document.canonical(document.snapshot(ts)) == document.canonical(immutable)
    same_path_bad = proposal(dict(op="patch_surface", surface_id=sid, mode="COLOR_ONLY", values=dict(color=[.1,.2,.3,1])),
        dict(op="set_channel", layer_id=fill(ts, sid).id, channel="BASE_COLOR", values=dict(color=[.1,.2,.3,.2])))
    require_rejection(lambda: commands.compile_proposal(ts, same_path_bad, "same-path-bad"), "overwrote")
    apply(ts, [dict(op="patch_surface", surface_id=sid, mode="PERIOD_ONLY", values=dict(period_m=.0005))], "finer")
    finer = field(ts, prop(fill(ts, sid)))
    assert np.max(abs(finer[:, 16:] - finer[:, :-16])) < .0001
    assert np.mean(abs(finer - value)) > .02
    assert prop(fill(ts, sid)).color[:] == (.03, .12, .04, 1) or np.allclose(prop(fill(ts,sid)).color, [.03,.12,.04,1])
    transactions.undo(ts)
    assert document.canonical(document.snapshot(ts)) == document.canonical(immutable)
    print("SURFACE_PATCH_INVARIANTS_OK", flush=True)

    # Coordinate a native effect and an ordinary existing fill using typed
    # appearance patches. No surface metadata migration is needed for the fill.
    from open_painter.evaluation.mask_eval import _evaluate_masks
    wet = apply(ts, [dict(op="create_surface", id="new:mud", name="Wet mud", family="MUD"),
        dict(op="add_layer", id="new:splash", name="Existing splashes", kind="FILL"),
        dict(op="set_channel", layer_id="new:splash", channel="BASE_COLOR", values=dict(color=[.05,.025,.01,1])),
        dict(op="set_channel", layer_id="new:splash", channel="ROUGHNESS", values=dict(value=.2)),
        dict(op="add_mask", id="new:splashmask", layer_id="new:splash", name="Stable splash placement", source="PROCEDURAL",
             values=dict(procedural="DOTS", noise_scale=4, seed=73))], "wet-effects")
    mud_id, splash_id = wet["aliases"]["new:mud"], wet["aliases"]["new:splash"]
    splash = next(l for l in ts.layers if l.id == splash_id)
    mud = fill(ts, mud_id)
    assert not splash.surface_id, "Custom fill identity is the existing layer id"
    report = next(s for s in surfaces.describe(ts) if s["id"] == splash_id)
    assert report["family"] == "CUSTOM" and "APPEARANCE_ONLY" in report["patch_modes"]
    before_dry = document.snapshot(ts)
    mask_before = _evaluate_masks(ts, splash, 512, 0, 512, {}, {})
    colors_before = [channel_eval.source(prop(l), "FILL", 512, 0, 512, {}, ts, {}) for l in (mud, splash)]
    rough_before = [channel_eval.source(prop(l, "ROUGHNESS"), "FILL", 512, 0, 512, {}, ts, {}) for l in (mud, splash)]
    apply(ts, [dict(op="patch_surface", surface_id=identifier, mode="APPEARANCE_ONLY",
                   values=dict(color=[.2,.13,.065,1], roughness_range=[.78,.82] if identifier == mud_id else [.8,.8]))
               for identifier in (mud_id, splash_id)], "coordinated-drying")
    for index, member in enumerate((mud, splash)):
        color_after = channel_eval.source(prop(member), "FILL", 512, 0, 512, {}, ts, {})
        rough_after = channel_eval.source(prop(member, "ROUGHNESS"), "FILL", 512, 0, 512, {}, ts, {})
        assert color_after[..., :3].mean() > 2 * colors_before[index][..., :3].mean()
        assert rough_after[..., 0].mean() > rough_before[index][..., 0].mean() + .4
    np.testing.assert_array_equal(mask_before, _evaluate_masks(ts, splash, 512, 0, 512, {}, {}))
    changed = document.changes(before_dry, document.snapshot(ts))
    assert all(path[2] == "channels" and path[-1] in {"color", "value", "procedural_detail"} for path, _, _ in changed), changed
    assert not splash.surface_id
    require_rejection(lambda: apply(ts, [dict(op="patch_surface", surface_id=splash_id, mode="PERIOD_ONLY",
                      values=dict(period_m=.001))], "legacy-custom-period"), "metric period")
    require_rejection(lambda: apply(ts, [dict(op="patch_surface", surface_id=splash_id, mode="APPEARANCE_ONLY",
                      values=dict(height_m=.0001))], "legacy-custom-height"), "legacy height")
    document.apply(ts, immutable)
    print("CUSTOM_NATIVE_COORDINATED_DRYING_OK", flush=True)

    # All nine typed recipes compile into native layers. Existing legacy fields
    # retain version0 and do not undergo automatic rescaling.
    for family in surfaces.FAMILIES:
        compiled = commands.compile_proposal(ts, proposal(dict(op="create_surface", id="new:test", name=family, family=family)), "family-" + family)
        assert all(l["type"] in {"FILL", "FOLDER"} for l in compiled["after"]["layers"])
        assert any(l.get("surface_family") == family for l in compiled["after"]["layers"])
    aliased = commands.compile_proposal(ts, proposal(
        dict(op="create_surface", id="new:alias", name="Editable finish", family="BARE_METAL"),
        dict(op="set_channel", layer_id="new:alias:finish", channel="METALLIC", values=dict(value=.8))), "child-alias")
    assert "new:alias:finish" in aliased["aliases"]
    assert next(p for l in aliased["after"]["layers"] if l["id"] == aliased["aliases"]["new:alias:finish"] for p in l["channels"] if p["channel"] == "METALLIC")["value"] == .8
    print("NINE_NATIVE_SURFACES_OK", flush=True)

    # Independent rotated members orient wood along their actual long axes.
    points = np.array([[x,y,z] for x in [-.001,.001] for y in [-.02,.02] for z in [-.001,.001]])
    triangles = np.array([[0,1,2],[1,2,3],[4,5,6],[5,6,7],[0,2,4],[2,4,6],[1,3,5],[3,5,7],[0,1,4],[1,4,5],[2,3,6],[3,6,7]])
    rotation = np.array([[0,-1,0],[1,0,0],[0,0,1]])
    merged = np.concatenate((points, points @ rotation.T + [1,0,0]))
    pairs = material_space.component_frames(merged, np.concatenate((triangles,triangles+8)))
    assert abs(np.dot(pairs[0][1][1], [0,1,0])) > .999
    assert abs(np.dot(pairs[-1][1][1], [1,0,0])) > .999
    print("MEMBER_FRAME_ROTATIONS_OK", flush=True)

    # Native band geometry retains width, spacing and source maps on translation.
    # Rotate plane so it has height in declaredZ; no source mesh changes needed.
    obj.rotation_euler.x = math.pi / 2
    bpy.context.view_layer.update()
    bid = apply(ts, [dict(op="create_bands", id="new:bands", name="Precise bands", centers=[.70,.78], width=.015, color=[.01,.03,.4,1])], "bands")["aliases"]["new:bands"]
    bands_before = next(s for s in surfaces.describe(ts) if s["id"] == bid)
    apply(ts, [dict(op="patch_surface", surface_id=bid, mode="PLACEMENT_ONLY", values=dict(band_translation=-.05))], "move-bands")
    bands_after = next(s for s in surfaces.describe(ts) if s["id"] == bid)
    np.testing.assert_allclose(bands_after["controls"]["centers"], [.65,.73], atol=1e-6)
    np.testing.assert_allclose(bands_before["controls"]["widths"], bands_after["controls"]["widths"], atol=1e-7)
    assert bands_before["mask_ids"] == bands_after["mask_ids"]
    print("PRECISE_BAND_TRANSLATION_OK", flush=True)

    # Different extents sharing one set retain the same physical period despite
    # different UV density. Each object gets its own non-overlapping halfatlas.
    document.apply(ts, original)
    obj.rotation_euler = (0,0,0)
    for corner in obj.data.uv_layers.active.data:
        corner.uv.x *= .5
    second = plane("Second extent", .032)
    second.location.x = .04
    for corner in second.data.uv_layers.active.data:
        corner.uv.x = .5 + corner.uv.x * .5
    sets.assign(scene, ts, second)
    bpy.context.view_layer.update()
    shared = field(ts, prop(fill(ts, sid)))
    assert np.max(abs(shared[:, 16:254] - shared[:, :238])) < .0001
    assert np.max(abs(shared[:, 264:510] - shared[:, 256:502])) < .0001
    print("MULTIPLE_EXTENTS_PHYSICAL_PERIOD_OK", flush=True)

    # Native serialization retains material-space and coherent source IDs.
    out = ROOT / ".blender-test" / "material-surfaces-save.blend"
    identity = ts.id
    saved_sources = surfaces.describe(ts)[0]["source_ids"]
    bpy.ops.wm.save_as_mainfile(filepath=str(out))
    bpy.ops.wm.open_mainfile(filepath=str(out))
    ts = next(t for t in bpy.context.scene.open_painter.texture_sets if t.id == identity)
    assert surfaces.describe(ts)[0]["source_ids"] == saved_sources
    assert physical_space.capture(ts)["status"] == "KNOWN"
    np.testing.assert_allclose(field(ts, prop(fill(ts, sid))), shared, atol=1e-6)
    print("MATERIAL_SPACE_SAVE_REOPEN_OK", flush=True)
    print("MATERIAL_SURFACES_PASS", flush=True)


run()
