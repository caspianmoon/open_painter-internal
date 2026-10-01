"""One generated appearance source with aligned, explicitly approximate scalar detail.

The original remains packed and editable. Supporting channels derive from the
same source pixels and share mapping; this is not measured or inferred PBR.
"""
import json

from .schema import AuthoringError
from ..utils.digest import sha256


def scalar_detail(rgb):
    """Bounded local luminance contrast; retain features without inventing channels."""
    import numpy as np
    rgb = np.asarray(rgb, dtype=np.float32)
    if rgb.ndim != 3 or rgb.shape[2] < 3 or not np.isfinite(rgb).all():
        raise AuthoringError("Generated surface pixels must be finite RGB")
    luminance = rgb[..., :3] @ np.array([.2126, .7152, .0722], np.float32)
    radius = max(1, min(16, min(luminance.shape) // 32))
    smooth = sum(np.roll(luminance, offset, axis=0) for offset in range(-radius, radius + 1)) / (2 * radius + 1)
    smooth = sum(np.roll(smooth, offset, axis=1) for offset in range(-radius, radius + 1)) / (2 * radius + 1)
    # A little broad structure retains scratches wider than the local window.
    detail = luminance - smooth + .15 * (smooth - float(smooth.mean()))
    amplitude = max(float(np.percentile(np.abs(detail), 98)), .015)
    return np.clip(.5 + .45 * detail / amplitude, 0, 1).astype(np.float32)


def assign(ts, layer, cmd, images):
    from .generated_textures import placeholder
    if layer["type"] != "FILL":
        raise AuthoringError("A generated surface source belongs on an editable fill layer")
    source = placeholder(cmd["image_id"], "sRGB", images)
    low, high = cmd["roughness_range"]
    if low > high:
        raise AuthoringError("Roughness range must be increasing")
    if high - low > .7:
        raise AuthoringError("Keep generated roughness variation within a 0.7 range")
    extent = cmd.get("physical_extent_m")
    shared = dict(projection=cmd.get("projection", "UV"), scale=cmd.get("scale", [1, 1]),
                  offset=cmd.get("offset", [0, 0]), rotation=cmd.get("rotation", 0))
    if extent:
        from .physical_space import require
        shared.update(material_mapping_version=1, material_meters_per_unit=require(ts),
                      material_image_extent_m=extent)
    else:
        shared["material_mapping_version"] = 0
    metadata = dict(version=1, type="GENERATED_SURFACE_SOURCE", name=cmd["name"],
        source_id=cmd["image_id"], purpose=cmd["purpose"], channels=["BASE_COLOR", "ROUGHNESS"],
        derivation="LOCAL_LUMINANCE_CONTRAST_V1", calibrated_bundle=False,
        provenance="GENERATED_WITH_ALIGNED_DERIVED_SCALARS", mapping_frame=shared,
        limitation="Roughness/relief are art-directed luminance approximations, not recovered physical measurements")
    relief = cmd.get("height_amplitude", 0)
    if relief:
        metadata["channels"].append("HEIGHT")
    for prop in layer["channels"]:
        channel = prop["channel"]
        if channel == "HEIGHT" and not relief:
            # A source explicitly without relief must not retain the native
            # surface recipe's unrelated procedural height underneath it.
            prop.update(enabled=False, image=None, detail_image=None, procedural="", anchor="")
            continue
        if channel not in metadata["channels"]:
            continue
        prop.update(**shared, enabled=True, image=None, detail_image=None, procedural="", anchor="",
                    material_source_id=cmd["image_id"], source_metadata=json.dumps(metadata, sort_keys=True))
        if channel == "BASE_COLOR":
            prop["image"] = source
        else:
            prop["detail_image"] = dict(**source, **{"$surface_detail": "LOCAL_LUMINANCE_CONTRAST_V1"})
            if channel == "ROUGHNESS":
                prop["value"] = (low + high) / 2
                prop["procedural_detail"] = (high - low) / .35
            else:
                prop["value"] = .5
                prop["procedural_height"] = relief
                if extent:
                    prop["material_height_m"] = relief * prop["material_meters_per_unit"] * ts.height_distance


def load_detail(value, images):
    import bpy
    import numpy as np
    from .assets import prepare
    from ..core.image_manager import pack_image
    source = prepare({k: v for k, v in value.items() if k != '$surface_detail'}, images=images)
    rgba = np.empty(len(source.pixels), np.float32)
    source.pixels.foreach_get(rgba)
    rgba = rgba.reshape(source.size[1], source.size[0], 4)
    digest = sha256(rgba.tobytes()).hexdigest()
    key = value["$generated"] + ":" + value["$surface_detail"] + ":" + digest
    for image in bpy.data.images:
        if image.get("director_surface_derivation") == key and not image.is_dirty and image.packed_file:
            return image
    field = rgba[..., 3] if value["$surface_detail"] == "ALPHA" else scalar_detail(rgba)
    if value.get('$surface_replacement') == 'LOCAL':
        # Percentile normalization is global; don't let a local edit shift relief
        # on the rest of the source. Retain the prior derived signal outside it.
        from .surface_replacement import window_mask
        previous = prepare(dict(value['reference'], **{'$surface_detail': value['$surface_detail']}), images=images) \
            if isinstance(value['reference'], dict) else None
        if previous is not None:
            old = np.empty(len(previous.pixels), np.float32)
            previous.pixels.foreach_get(old)
            old = old.reshape(previous.size[1], previous.size[0], 4)[..., 0]
        else:
            original = np.empty(len(value['reference'].pixels), np.float32)
            value['reference'].pixels.foreach_get(original)
            original = original.reshape(value['reference'].size[1], value['reference'].size[0], 4)
            old = original[..., 3] if value['$surface_detail'] == 'ALPHA' else scalar_detail(original)
        mask = window_mask(field.shape, value['windows'])
        field = old*(1-mask) + field*mask
    output = np.ones_like(rgba)
    output[..., :3] = field[..., None]
    image = bpy.data.images.new("Director aligned detail " + digest[:12], width=source.size[0], height=source.size[1], float_buffer=True)
    image.colorspace_settings.name = "Non-Color"
    image.pixels.foreach_set(output.ravel())
    image["director_image_id"] = value["$generated"]
    image["director_surface_derivation"] = key
    image["director_surface_source_sha256"] = digest
    image["director_surface_note"] = "Aligned luminance approximation; not calibrated PBR"
    image.use_fake_user = True
    pack_image(image)
    return image


def detail_layer(ts, cmd, identifier, parent, objects, images):
    """A nonrepeating generated detail with editable placement and region masks."""
    from . import assets, document, regions
    from .generated_textures import placeholder
    source = placeholder(cmd["image_id"], "sRGB", images)
    layer = assets.new_layer(identifier, cmd["name"], "FILL", parent)
    mapping = dict(projection=cmd.get("projection", "UV"), scale=cmd.get("scale", [1, 1]),
                   offset=cmd.get("offset", [0, 0]), rotation=cmd.get("rotation", 0), tiling="NONE")
    metadata = json.dumps(dict(type="GENERATED_DETAIL", source_id=cmd["image_id"],
                              purpose=cmd["purpose"], fictional_markings=True), sort_keys=True)
    for prop in layer["channels"]:
        if prop["channel"] == "BASE_COLOR":
            prop.update(**mapping, image=source, source_metadata=metadata)
        elif prop["channel"] == "ROUGHNESS":
            prop["value"] = cmd.get("roughness", .65)
        elif prop["channel"] == "METALLIC":
            prop["value"] = 0
    alpha = document.defaults("OP_Mask")
    alpha.update(**mapping, id=identifier + ".alpha", name="Generated detail silhouette", source="FILL",
                 image=dict(**source, **{"$surface_detail": "ALPHA"}))
    region = document.defaults("OP_Mask")
    region.update(id=identifier + ".region", name="Detail placement region", source="MESH", operation="MULTIPLY")
    region["mesh"].update(regions.selector(ts, cmd["region_id"], objects))
    layer["masks"].extend((alpha, region))
    return layer
