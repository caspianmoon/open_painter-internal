"""Scoped existing-image bundles and truthful source provenance.

An imported bundle is an explicit artist/provider declaration of coordinated
channels. Shared mapping is enforced; semantic correctness of external bitmap
content is not inferred from matching dimensions or file names.
"""
import hashlib
import json

from .schema import AuthoringError


def _id(image):
    text = f"{image.library.filepath if image.library else ''}|{image.name_full}"
    return "source:" + hashlib.sha256(text.encode()).hexdigest()[:24]


def bindings(ts):
    result = {}
    for layer in ts.layers:
        for prop in layer.channels:
            for image in (prop.image, prop.detail_image):
                if image:
                    result[_id(image)] = image
        for mask in layer.masks:
            if mask.image:
                result[_id(mask.image)] = mask.image
    return result


def describe(ts):
    return [dict(id=identifier, name=image.name, dimensions=list(image.size),
                 color_space=image.colorspace_settings.name,
                 provenance="GENERATED" if image.get("director_image_id") else "EXISTING_IMPORTED",
                 revision=image.get("open_painter_pixel_revision", 0))
            for identifier, image in bindings(ts).items()]


def assign(ts, layer, cmd, bundle_id, generated):
    if layer["type"] != "FILL":
        raise AuthoringError("A PBR bundle belongs to a fill layer")
    items = cmd["sources"]
    if len(items) < 2:
        raise AuthoringError("A bundle needs at least two declared channels")
    known = bindings(ts)
    mapped, dimensions, generated_any = {}, set(), False
    for channel, identifier in items.items():
        color_space = "sRGB" if channel in {"BASE_COLOR", "EMISSION"} else "Non-Color"
        if identifier in known:
            image = known[identifier]
            if min(image.size) < 1:
                raise AuthoringError("Bundle image has no decoded pixel dimensions")
            dimensions.add(tuple(image.size))
            generated_any |= bool(image.get("director_image_id"))
            mapped[channel] = dict(image={"$bundle_image": image, "color_space": color_space},
                                   revision=image.get("open_painter_pixel_revision", 0), source_id=identifier)
        elif identifier in (generated or {}):
            from .generated_textures import placeholder
            entry = generated[identifier]
            size = entry.get("size") or [entry.get("width", 0), entry.get("height", 0)]
            if min(size) < 1:
                raise AuthoringError("Generated bundle image lacks verified dimensions")
            dimensions.add(tuple(size))
            generated_any = True
            mapped[channel] = dict(image=placeholder(identifier, color_space, generated),
                                   revision=entry.get("sha256", identifier), source_id=identifier)
        else:
            raise AuthoringError("Bundle source is outside this material or instruction")
    if len(dimensions) != 1:
        raise AuthoringError("A coherent bundle requires matching decoded image dimensions")
    # Reuse the first declared native channel's frame (BASE_COLOR when present)
    # so a bundle added to member-aligned wood does not rotate its color away
    # from the existing pore/height structure. All declared maps share sampling.
    reference = next(prop for prop in layer["channels"] if prop["channel"] in mapped)
    metric = "physical_extent_m" in cmd
    if metric:
        from .physical_space import require
        meters = require(ts)
        if any(key in cmd for key in ("scale", "offset", "rotation")) or cmd.get("projection", "TRIPLANAR") != "TRIPLANAR":
            raise AuthoringError("Metric bundle extent replaces UV transforms; choose metric extent or shared UV mapping")
        if "NORMAL" in items:
            raise AuthoringError("Tangent-normal bundles require shared UV mapping; use metric height for world-frame relief")
    shared = dict(projection=cmd.get("projection", "TRIPLANAR" if metric else "UV"), scale=cmd.get("scale", [1, 1]),
                  offset=cmd.get("offset", [0, 0]), rotation=cmd.get("rotation", 0),
                  filtering=reference["filtering"], tiling=reference["tiling"],
                  triplanar_hardness=reference["triplanar_hardness"])
    physical_frame = {key: reference[key] for key in
                      ("material_frame", "material_origin", "material_axis_u", "material_axis_v", "material_directional")}
    if reference.get("material_mapping_version") != 1:
        physical_frame.update(material_frame="OBJECT", material_origin=[0, 0, 0],
                              material_axis_u=[1, 0, 0], material_axis_v=[0, 1, 0], material_directional=False)
    for prop in layer["channels"]:
        key = prop["channel"]
        if key not in mapped:
            continue
        item = mapped[key]
        prop.update(**shared, image=item["image"], detail_image=None, anchor="", procedural="",
                    enabled=True, material_mapping_version=1 if metric else 0, material_source_id=item["source_id"])
        if metric:
            prop.update(material_meters_per_unit=meters, material_image_extent_m=cmd["physical_extent_m"],
                        **physical_frame)
        prop["source_metadata"] = json.dumps(dict(version=1, bundle_id=bundle_id,
            source_id=item["source_id"], purpose="Coordinated PBR channel", channels=[key],
            interpretation="sRGB color" if key in {"BASE_COLOR", "EMISSION"} else "Linear data",
            mapping_frame=dict(shared, **physical_frame) if metric else shared,
            physical_extent_m=cmd.get("physical_extent_m"),
            provenance="GENERATED_APPROXIMATION" if generated_any else "DECLARED_IMPORTED_BUNDLE",
            revision_identity=item["revision"], dimensions=list(next(iter(dimensions))),
            calibrated_bundle=False,
            note="Shared transform is enforced. Physical channel calibration, seams and baked lighting require inspection."), sort_keys=True)


def prepare(value):
    """Copy/retag data maps without changing an artist's source image anywhere."""
    image, color_space = value["$bundle_image"], value["color_space"]
    if image.colorspace_settings.name == color_space:
        return image
    duplicate = image.copy()
    duplicate.name = image.name + " · " + color_space
    duplicate.colorspace_settings.name = color_space
    # The source can be a file or generated float buffer. A packed copy retains
    # the exact imported pixels through save/reopen without external paths.
    duplicate.pack()
    duplicate.use_fake_user = True
    return duplicate
