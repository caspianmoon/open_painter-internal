"""Shared, packed scalar source images; never rasterize parameter variants here."""

import math
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1] / "assets" / "generated"
SOURCE_IDS = frozenset({
    "brushed_metal", "fine_wood", "leather_grain", "concrete_pores",
    "fabric_weave", "rust_detail", "polymer_grain", "surface_smudges",
    "machined_scratches", "cast_iron_grain", "brass_tarnish", "burn_blisters",
    "zinc_spangle", "marble_veins", "granite_minerals",
    "abrasion_mask_v2", "brushed_relief_v2", "paint_loss_v2", "rust_relief_v2",
    "timber_relief_v2", "leather_relief_v2", "denim_twill_v2", "cement_relief_v2",
    "charred_timber_v2",
    "weld_bead_v2", "burn_relief_v2",
    "rubber_cracks_v2", "grease_deposits_v2",
})


def validate(spec):
    if not isinstance(spec, dict) or spec.get("id") not in SOURCE_IDS:
        raise ValueError("Unknown generated surface-detail source.")
    if set(spec) - {"id", "scale", "color", "roughness", "height"}:
        raise ValueError("Unknown generated source parameter.")
    scale = spec.get("scale", (1, 1))
    if (len(scale) != 2 or any(not math.isfinite(v) or not .001 <= v <= 512 for v in scale)):
        raise ValueError("Invalid generated source scale.")
    for name, lo, hi in (("color", 0, 2), ("roughness", 0, .7), ("height", -1, 1)):
        value = spec.get(name, 0)
        if not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError("Invalid generated source amplitude: " + name)
    if not (ROOT / (spec["id"] + ".png")).is_file():
        raise ValueError("Bundled surface-detail image is missing: " + spec["id"])


def load(identifier):
    if identifier not in SOURCE_IDS:
        raise ValueError("Unknown generated surface-detail source.")
    # Read-only library inputs are shared across layers and texture sets. A
    # packed copy keeps .blend projects independent of the installed extension.
    path = ROOT / (identifier + ".png")
    for image in bpy.data.images:
        if (image.get("open_painter_generated_source") == identifier
                and image.packed_file and image.colorspace_settings.name == "Non-Color"):
            return image
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.name = "Open Painter Detail - " + identifier.replace("_", " ").title()
        image.colorspace_settings.name = "Non-Color"
        image["open_painter_generated_source"] = identifier
        image["open_painter_source_provenance"] = "AI-generated; see assets/generated/provenance.json"
        image.pack()
        return image
    except Exception:
        bpy.data.images.remove(image)
        raise


def discard_unused_new_images(before):
    for image in list(bpy.data.images):
        if (image.as_pointer() not in before and image.get("open_painter_generated_source")
                and not image.users):
            bpy.data.images.remove(image)
