"""Export padding and resampling on real geometry (quality remedy M2A, plan §6.7).

Runs the exporter on the quality fixtures and checks: every padding mode at 512-4K (8K as a
bounded scratch case on the pure transform), two islands keep their own values, zeros and
black survive, the transparency card keeps its cutout, mirrored/border/close islands, resize
against an independently filtered reference field, normal angular error, packed alpha,
invalidation after a UV edit, byte-identical repeat exports, and untouched authoring images.
"""
import hashlib
import json
import sys
import tempfile
import tracemalloc
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests/integration"))
import open_painter
import quality_fixtures as fixtures
from open_painter.core import image_manager as images, layer_stack as stack
from open_painter.evaluation.evaluator import LayerEvaluator
from open_painter.export import padding
from open_painter.export.exporter import export

open_painter.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
project = scene.open_painter
REPORT = ROOT / ".blender-test/quality/export-padding.json"
REPORT.parent.mkdir(parents=True, exist_ok=True)
report = {"blender": bpy.app.version_string, "checks": []}


def check(name, **facts):
    report["checks"].append({"name": name, **facts})
    print(f"EXPORT_PADDING {name}: PASS {facts}", flush=True)


def read(path, non_color=True):
    image = bpy.data.images.load(str(path))
    if non_color:
        image.colorspace_settings.name = "Non-Color"
    data = images.pixels(image).copy()      # Blender loads files bottom-left, as the exporter's arrays are
    bpy.data.images.remove(image)
    return data


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def authoring_hashes(ts):
    result = {}
    for layer in ts.layers:
        for prop in layer.channels:
            if prop.image is not None:
                result[(layer.name, prop.channel)] = hashlib.sha256(images.pixels(prop.image).tobytes()).hexdigest()
    return result


def do_export(ts, folder, mode="AUTOMATIC", pixels=16, resolution="SET", preset="BLENDER", bit_depth="8"):
    ts.export.padding_mode, ts.export.padding_pixels = mode, pixels
    ts.export.resolution, ts.export.preset, ts.export.file_format, ts.export.bit_depth = resolution, preset, "PNG", bit_depth
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = export(bpy.context, ts, ts.export, directory=folder, overwrite=True)
    return {p.name: p for p in paths}


def by_label(files, label):
    return next(p for name, p in files.items() if label in name)


# ---- 1. two unrelated islands: interior kept, gutter ownership deterministic, all modes/resolutions ----
plate, expected = fixtures.channel_plate(scene, resolution=512)
atlas, quads = fixtures.difficult_atlas(scene, resolution=512)
with tempfile.TemporaryDirectory(prefix="op-export-padding-") as tmp:
    root = Path(tmp)
    n = 512
    LayerEvaluator(scene).evaluate_texture_set(atlas.id, force=True)
    own = padding.ownership(atlas, n)
    assert own.diagnostics["islands"] >= 8, own.diagnostics
    files = do_export(atlas, root / "atlas-auto")
    base = read(by_label(files, "BaseColor"))[..., :3]
    covered, island = own.covered, own.island
    # Interior texels equal the evaluated output exactly (8-bit quantised) in every mode.
    evaluated = images.pixels(next(o.image for o in atlas.outputs if o.channel == "BASE_COLOR"))[..., :3]
    quantised = np.round(np.clip(evaluated, 0, 1) * 255) / 255
    reports = {}
    for mode, pixels in (("AUTOMATIC", 0), ("CUSTOM", 3), ("FILL", 0), ("NONE", 0)):
        files = do_export(atlas, root / f"atlas-{mode}", mode=mode, pixels=pixels)
        reports[mode] = (atlas.export.last_report, atlas.export.last_report_detail)
        exported = read(by_label(files, "BaseColor"))[..., :3]
        # The base colour is written sRGB-encoded; compare through the same encoding.
        srgb = np.where(quantised <= .0031308, quantised * 12.92, 1.055 * np.power(np.maximum(quantised, 1e-8), 1 / 2.4) - .055)
        srgb_q = np.round(srgb * 255) / 255
        interior_error = float(np.abs(exported[covered] - srgb_q[covered]).max())
        assert interior_error <= 1.5 / 255, (mode, interior_error)
        donor, d2 = padding.nearest_donors(covered, island, padding.effective_padding(atlas.export, n))
        padded = donor >= 0
        if mode != "NONE":
            assert padded.any()
            # A padded texel equals its donor's exported value: a copy, never a blend.
            flat = exported.reshape(-1, 3)
            assert np.array_equal(flat[padded.ravel()], flat[donor.ravel()[padded.ravel()]]), mode
        else:
            assert not padded.any()
        report["checks"].append({"name": f"atlas {mode}", "padded_texels": int(padded.sum()), "interior_error": interior_error,
                                 "report": atlas.export.last_report})
    check("all modes keep the interior and copy donors", islands=own.diagnostics["islands"],
          gutter_report=reports["AUTOMATIC"][0])
    # Fill Empty Space, per channel (hardening H3): every empty texel of every map is a copy of a
    # valid texel, for colour, normal, height, the packed Unreal ORM and the URP smoothness alpha.
    donor, _ = padding.nearest_donors(covered, island, "FILL")
    assert padding.unresolved_texels(covered, donor) == 0 and padding.donors_valid(covered, donor)
    padded = (donor >= 0).ravel()
    for preset, labels in (("BLENDER", ("BaseColor", "Normal", "Height", "Roughness", "Metallic", "Opacity")),
                           ("UNREAL", ("ORM", "Normal")), ("UNITY_URP", ("MetallicGlossMap", "BaseMap"))):
        files = do_export(atlas, root / f"atlas-fill-{preset}", mode="FILL", preset=preset)
        for label in labels:
            exported = read(by_label(files, label)).reshape(-1, 4)
            assert np.array_equal(exported[padded], exported[donor.ravel()[padded]]), (preset, label)
        detail = json.loads(atlas.export.last_report_detail)
        assert detail.get("unresolved_texels") == 0, detail
    check("fill empty space is complete per channel", presets=["BLENDER", "UNREAL", "UNITY_URP"])
    assert "closer than" in reports["AUTOMATIC"][0], reports["AUTOMATIC"][0]   # the close pair is reported
    assert reports["NONE"][0] == "UV padding: none", reports["NONE"][0]
    detail = json.loads(reports["AUTOMATIC"][1])
    assert detail["gutters"]["tight_islands"], detail["gutters"]
    check("tight gutters are reported", tight=detail["gutters"]["tight_islands"],
          narrowest=detail["gutters"]["narrowest_gutter_px"])

    # Resolutions 512 → 4K (the plate: one island) and 8K on the pure transform as a bounded case.
    for size in ("1024", "2048", "4096"):
        files = do_export(plate, root / f"plate-{size}", resolution=size)
        exported = read(by_label(files, "Roughness"))
        assert exported.shape[0] == int(size)
        own_out = padding.ownership(plate, int(size))
        assert own_out.covered.all(), "the plate fills its UV square"
    tracemalloc.start()
    n8 = 8192
    covered8 = np.zeros((n8, n8), bool); covered8[1000:5000, 1000:3000] = True
    island8 = np.where(covered8, 0, -1).astype(np.int32)
    donor8, _ = padding.nearest_donors(covered8, island8, 64)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del donor8
    check("8K transform is bounded", scratch_mib=round((peak - covered8.nbytes - island8.nbytes) / 2 ** 20))
    del covered8, island8

    # ---- 2. black, zero and empty: valid texels keep their values; an empty layer stack still pads ----
    mud = stack.add(plate, "PAINT", "Black")
    black = fixtures.paint_layer_image(plate, mud, "BASE_COLOR")
    images.write(black, np.zeros((512, 512, 4), np.float32) + np.array((0, 0, 0, 1), np.float32))
    stack.channel_for(mud, "BASE_COLOR").enabled = True
    files = do_export(plate, root / "plate-black")
    exported = read(by_label(files, "BaseColor"))
    assert exported[..., :3].max() == 0.0
    check("black surfaces stay black")
    stack.remove(plate, stack.index_of(plate, mud.id))

    # ---- 3. the transparency card: RGB extends, the cutout's coverage does not inflate ----
    card, opacity = fixtures.transparency_card(scene, resolution=512)
    files = do_export(card, root / "card", preset="GLTF")
    colour = read(next(p for name, p in files.items() if "BaseColor" in name or "baseColor" in name), non_color=False)
    own_card = padding.ownership(card, 512)
    inside = own_card.covered
    exported_alpha = colour[..., 3]
    reference_alpha = np.round(np.clip(opacity, 0, 1) * 255) / 255
    assert np.abs(exported_alpha[inside] - reference_alpha[inside]).max() <= 1.5 / 255, "cutout coverage changed"
    check("transparency card keeps its cutout", inside_texels=int(inside.sum()))

    # ---- 4. resize against an independent reference field ----
    seam, field = fixtures.seam_object(scene, resolution=2048)
    LayerEvaluator(scene).evaluate_texture_set(seam.id, force=True)
    source = images.pixels(next(o.image for o in seam.outputs if o.channel == "ROUGHNESS"))
    own_src = padding.ownership(seam, 2048)
    files = do_export(seam, root / "seam-512", resolution="512", mode="NONE")
    exported = read(by_label(files, "Roughness"))[..., 0]
    own_512 = padding.ownership(seam, 512)
    # Independent reference: coverage-weighted box filter of the source over 4x4 blocks.
    w = own_src.covered.astype(np.float32)
    num = (source[..., 0] * w).reshape(512, 4, 512, 4).sum(axis=(1, 3))
    den = w.reshape(512, 4, 512, 4).sum(axis=(1, 3))
    reference = np.divide(num, den, out=np.zeros_like(num), where=den > 0)
    valid = own_512.covered & (den > 0)
    error = np.abs(exported[valid] - np.round(reference[valid] * 255) / 255)
    assert float(error.max()) <= 1.5 / 255, float(error.max())
    # Fractional boundary blocks: the plain box filter differs from the reference exactly there.
    partial = valid & (den < 16)
    plain = source[..., 0].reshape(512, 4, 512, 4).mean(axis=(1, 3))
    assert partial.any() and np.abs(plain[partial] - reference[partial]).max() > .02
    check("downsample matches the coverage-weighted reference", max_error=float(error.max()),
          fractional_blocks=int(partial.sum()))
    files = do_export(seam, root / "seam-4096", resolution="4096", mode="NONE")
    up = read(by_label(files, "Roughness"))[..., 0]
    own_4096 = padding.ownership(seam, 4096)
    # Upsampling: every valid output texel lies within the source's value range of its 2x2 neighbourhood.
    inner = own_4096.covered.copy()
    inner[:2] = inner[-2:] = inner[:, :2] = inner[:, -2:] = False
    assert up[inner].min() >= source[..., 0][own_src.covered].min() - 1.5 / 255
    check("upsample stays within the valid source range")

    # ---- 5. normal angular error and packed alpha ----
    files = do_export(seam, root / "seam-normal", resolution="1024")
    normal = read(by_label(files, "Normal"))[..., :3] * 2 - 1
    own_1024 = padding.ownership(seam, 1024)
    lengths = np.linalg.norm(normal, axis=-1)
    assert np.abs(lengths[own_1024.covered] - 1).max() < .03, float(np.abs(lengths[own_1024.covered] - 1).max())
    # Reference: the 2K normal field decoded, box-filtered over covered texels, renormalised.
    source_normal = images.pixels(next(o.image for o in seam.outputs if o.channel == "NORMAL"))[..., :3] * 2 - 1
    from open_painter.export.surface_normal import combined
    height = images.pixels(next(o.image for o in seam.outputs if o.channel == "HEIGHT"))
    wn = own_src.covered.astype(np.float32)[..., None]
    filtered = (source_normal * wn).reshape(1024, 2, 1024, 2, 3).sum(axis=(1, 3))
    weight = wn.reshape(1024, 2, 1024, 2, 1).sum(axis=(1, 3))
    filtered = np.divide(filtered, weight, out=np.zeros_like(filtered), where=weight > 0)
    height_1024, _ = padding.resample(height, own_src.covered, 1024)
    encoded = np.zeros((1024, 1024, 4), np.float32)
    encoded[..., :3] = filtered * .5 + .5
    reference_normal = combined(seam, encoded, height_1024)[..., :3] * 2 - 1
    reference_normal /= np.maximum(np.linalg.norm(reference_normal, axis=-1, keepdims=True), 1e-6)
    cosine = np.clip((normal * reference_normal).sum(axis=-1) / np.maximum(lengths, 1e-6), -1, 1)
    angle = np.degrees(np.arccos(cosine))
    valid = own_1024.covered & (weight[..., 0] > 0)
    assert float(np.percentile(angle[valid], 99)) < 2.0, float(np.percentile(angle[valid], 99))
    check("normal angular error", p99_degrees=round(float(np.percentile(angle[valid], 99)), 3),
          max_degrees=round(float(angle[valid].max()), 3))
    files = do_export(seam, root / "seam-unity", resolution="1024", preset="UNITY_URP")
    packed = read(next(p for name, p in files.items() if "Metallic" in name))
    rough = read(by_label(do_export(seam, root / "seam-blender", resolution="1024"), "Roughness"))[..., 0]
    assert np.abs(packed[own_1024.covered, 3] - (1 - rough[own_1024.covered])).max() <= 1.5 / 255, "packed alpha (smoothness) lost"
    donor, _ = padding.nearest_donors(own_1024.covered, own_1024.island, padding.automatic_padding(1024))
    padded = donor >= 0
    flat_packed = packed.reshape(-1, 4)
    assert np.array_equal(flat_packed[padded.ravel()], flat_packed[donor.ravel()[padded.ravel()]]), "packed gutter is not a copy"
    check("packed maps pad each semantic channel then pack")

    # ---- 6. determinism, untouched authoring, invalidation after a UV edit ----
    before_hashes = authoring_hashes(seam)
    first = {name: sha(p) for name, p in do_export(seam, root / "seam-repeat-1", resolution="1024").items()}
    second = {name: sha(p) for name, p in do_export(seam, root / "seam-repeat-2", resolution="1024").items()}
    assert first == second, "repeat exports differ"
    assert authoring_hashes(seam) == before_hashes, "export changed an authoring image"
    check("repeat exports are byte-identical and authoring images untouched", files=len(first))
    own_before = padding.ownership(seam, 1024)
    obj = next(ref.object for ref in seam.assignments)
    uv_layer = obj.data.uv_layers.active
    coords = np.empty(len(uv_layer.data) * 2, np.float32)
    uv_layer.data.foreach_get("uv", coords)
    coords = coords.reshape(-1, 2)
    coords[:, 0] = np.clip(coords[:, 0] * .9, 0, 1)     # squeeze the layout horizontally
    uv_layer.data.foreach_set("uv", coords.ravel())
    obj.data.update()
    bpy.context.view_layer.update()
    from open_painter.evaluation import dependencies
    dependencies.image_written  # noqa: the mesh epoch bumps through Blender's depsgraph handler
    own_after = padding.ownership(seam, 1024)
    assert own_after is not own_before and not np.array_equal(own_after.covered, own_before.covered), "a UV edit must re-derive ownership"
    check("UV edit invalidates the ownership map")

REPORT.write_text(json.dumps(report, indent=2, default=str))
print("EXPORT_PADDING_PASS", REPORT, flush=True)
